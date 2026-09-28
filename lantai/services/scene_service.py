"""scene 聚合层 service（ADR-0012，借鉴 TencentDB Agent Memory L2 场景层）。

写入侧：embedding 余弦聚类成簇（确定性优先）+ LLM 批量命名/摘要（失败降级代表 key，
宁 miss 不脏写）；幂等全量重建（POST /scenes/rebuild）。
读取侧：scene_navigation 纯函数生成导航块（场景名 + 摘要 + 成员 key），渐进式披露，
需要详情用 scene_get 下钻。
"""

import math

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.text import apply_recall_budget, truncate_codepoints
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, MemoryScene
from lantai.storage import db

_FALLBACK_SUMMARY_CHARS = 120  # LLM 命名失败时用代表内容截断作摘要


def cosine_sim(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯函数）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def cluster_scenes(items: list, vectors: list[list[float]], threshold: float) -> list[list]:
    """确定性贪心聚类（纯函数）：按输入顺序，与既有簇质心余弦 ≥ 阈值则并入，否则开新簇。

    返回 [[item, ...], ...]，簇内顺序与输入一致；阈值越高簇越细。
    """
    clusters: list[list] = []
    cluster_vecs: list[list[list[float]]] = []
    centroids: list[list[float]] = []
    for item, vec in zip(items, vectors, strict=False):
        best_idx, best_sim = -1, threshold
        for i, centroid in enumerate(centroids):
            s = cosine_sim(vec, centroid)
            if s >= best_sim:
                best_idx, best_sim = i, s
        if best_idx == -1:
            clusters.append([item])
            cluster_vecs.append([vec])
            centroids.append(list(vec))
        else:
            clusters[best_idx].append(item)
            cluster_vecs[best_idx].append(vec)
            n = len(cluster_vecs[best_idx])
            centroids[best_idx] = [
                sum(v[j] for v in cluster_vecs[best_idx]) / n for j in range(len(vec))
            ]
    return clusters


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """簇质心（纯函数）：成员向量均值；空列表返回 []。"""
    if not vectors:
        return []
    dim = len(vectors[0])
    return [sum(v[j] for v in vectors) / len(vectors) for j in range(dim)]


def incremental_cluster(
    vector: list[float], centroids: list[list[float]], threshold: float
) -> tuple[int | None, float]:
    """增量聚类（纯函数）：与既有场景质心余弦 ≥ 阈值返回最优簇下标，否则 (None, best_sim)。

    与 cluster_scenes 贪心并入规则一致（并入最相似簇）；未命中不建新场景
    （宁 miss 不脏写：保持无 scene_id 平铺行为）。
    """
    best_idx, best_sim = None, -1.0
    for i, centroid in enumerate(centroids):
        sim = cosine_sim(vector, centroid)
        if sim > best_sim:
            best_idx, best_sim = i, sim
    if best_sim >= threshold:
        return best_idx, best_sim
    return None, best_sim


def pick_representative(items: list) -> MemoryItem:
    """簇代表（纯函数）：use_count 最大，并列取 importance 大者。"""
    return max(items, key=lambda m: ((m.use_count or 0), (m.importance or 0.0)))


def _fallback_name(items: list) -> str:
    return (pick_representative(items).key or "").strip()


def _fallback_summary(items: list) -> str:
    content = (pick_representative(items).content or "").strip()
    return content[:_FALLBACK_SUMMARY_CHARS]


def _name_scenes(clusters: list[list]) -> list[tuple[str, str]]:
    """LLM 批量命名/摘要；失败或数量不符 → 代表 key 兜底（宁 miss 不脏写）。"""
    fallback = [(_fallback_name(c), _fallback_summary(c)) for c in clusters]
    if not settings.SCENE_REBUILD_LLM_NAMING:
        return fallback
    try:
        from lantai.llm.client import chat_json
        from lantai.llm.prompts import SCENE_NAMING_SYS
        from lantai.services.prompt_service import get_prompt

        reps = [(_fallback_name(c) or "无") for c in clusters]
        user = "\n".join(f"{i + 1}. {k}" for i, k in enumerate(reps))
        data = chat_json(SCENE_NAMING_SYS, user)
        items_out = data.get("scenes") or []
        if not isinstance(items_out, list) or len(items_out) != len(clusters):
            return fallback
        out = []
        for cluster, item in zip(clusters, items_out, strict=False):
            if not isinstance(item, dict):
                return fallback
            name = (item.get("name") or "").strip()
            summary = (item.get("summary") or "").strip()
            out.append((name or _fallback_name(cluster), summary or _fallback_summary(cluster)))
        return out
    except Exception:
        return fallback


def rebuild_scenes(threshold: float | None = None) -> dict:
    """幂等全量重建：清空旧场景 → 聚类 → 命名 → 落库并回写 scene_id。

    单成员簇不建场景（宁 miss 不脏写：未聚合的记忆保持无 scene_id 平铺行为）。
    返回 {"ok", "scene_count", "member_count"}。
    """
    thr = threshold if threshold is not None else settings.SCENE_CLUSTER_THRESHOLD
    with db.get_session() as s:
        items = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
        # 清空旧场景与归属（幂等全量重建）
        for sc in s.exec(select(MemoryScene)).all():
            s.delete(sc)
        for m in items:
            m.scene_id = None
        if not items:
            s.commit()
            return {"ok": True, "scene_count": 0, "member_count": 0}
        from lantai.llm.client import embed  # 外部依赖：允许 mock

        vectors = embed([m.content for m in items])
        vec_map = {m.id: vec for m, vec in zip(items, vectors, strict=False)}
        clusters = [c for c in cluster_scenes(items, vectors, thr) if len(c) >= 2]
        named = _name_scenes(clusters)
        now = utcnow()
        scene_count = 0
        for members, (name, summary) in zip(clusters, named, strict=False):
            scene = MemoryScene(
                id=new_id("scene"),
                name=name or "未命名场景",
                summary=summary or "",
                heat=sum((getattr(m, "use_count", 0) or 0) for m in members),
                member_count=len(members),
                centroid=_mean_vector([vec_map[m.id] for m in members]),
                created_at=now,
                updated_at=now,
            )
            s.add(scene)
            scene_count += 1
            for m in members:
                m.scene_id = scene.id
        s.commit()
        return {
            "ok": True,
            "scene_count": scene_count,
            "member_count": sum(len(c) for c in clusters),
        }


def _refresh_scene_stats(s, scene: MemoryScene) -> None:
    """场景热值重算（零写放大：heat = 成员 use_count 求和，member_count = 成员数）。"""
    members = s.exec(
        select(MemoryItem).where(MemoryItem.scene_id == scene.id, MemoryItem.status == "active")
    ).all()
    scene.heat = sum((getattr(m, "use_count", 0) or 0) for m in members)
    scene.member_count = len(members)


def assign_new_memory(memory_id: str, threshold: float | None = None) -> dict:
    """增量聚类归属：新记忆 embedding 与既有场景质心比较，命中并入并刷热值。

    未命中不建新场景、不强制归属（宁 miss 不脏写：保持平铺，等全量 rebuild 或
    手动 /scenes/assign）。返回 {"ok", "assigned", "scene_id", "best_sim"}。
    """
    thr = threshold if threshold is not None else settings.SCENE_CLUSTER_THRESHOLD
    with db.get_session() as s:
        m = s.get(MemoryItem, memory_id)
        if not m or m.status != "active":
            return {"ok": False, "assigned": False, "scene_id": None, "best_sim": 0.0}
        scenes = s.exec(select(MemoryScene)).all()
        if not scenes:
            return {"ok": True, "assigned": False, "scene_id": None, "best_sim": 0.0}
        from lantai.llm.client import embed  # 外部依赖：允许 mock

        vector = embed([m.content])[0]
        idx, best_sim = incremental_cluster(vector, [sc.centroid or [] for sc in scenes], thr)
        if idx is None:
            return {"ok": True, "assigned": False, "scene_id": None, "best_sim": best_sim}
        scene = scenes[idx]
        m.scene_id = scene.id
        _refresh_scene_stats(s, scene)
        s.add(m)
        s.commit()
        return {"ok": True, "assigned": True, "scene_id": scene.id, "best_sim": best_sim}


def assign_unassigned(limit: int = 50, threshold: float | None = None) -> dict:
    """补跑增量聚类：扫描无 scene_id 的 active 记忆逐条归属（消化期与手动同源）。

    不建新场景（宁 miss 不脏写）；返回 {"ok", "scanned", "assigned", "missed"}。
    """
    thr = threshold if threshold is not None else settings.SCENE_CLUSTER_THRESHOLD
    with db.get_session() as s:
        unassigned = s.exec(
            select(MemoryItem)
            .where(MemoryItem.status == "active", MemoryItem.scene_id.is_(None))
            .limit(limit)
        ).all()
    assigned, missed = 0, 0
    for m in unassigned:
        r = assign_new_memory(m.id, threshold=thr)
        if r.get("assigned"):
            assigned += 1
        else:
            missed += 1
    return {"ok": True, "scanned": len(unassigned), "assigned": assigned, "missed": missed}


def _scene_visible_member_ids(s, scene_id: str, principal) -> set[str]:
    """场景内对 `principal` 可见的成员 id 集合（票 `.scratch/mcp-identity-gaps/01b`）。

    可见 = `user_id == viewer OR IS NULL`（同 `_overview_scope` 口径：NULL
    老行可见——单人部署下大部分行无属主，判不可见等于场景全空）。
    """
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    rows = s.exec(
        select(MemoryItem.id).where(
            MemoryItem.scene_id == scene_id,
            MemoryItem.status == "active",
            (MemoryItem.user_id == viewer) | MemoryItem.user_id.is_(None),
        )
    ).all()
    return set(rows)


def get_scene(scene_id: str, principal=None) -> dict:
    """场景 + 成员详情（MCP scene_get / REST GET /scenes/{id} 下钻同源）。

    归属（票 `.scratch/mcp-identity-gaps/01b`）：`MemoryScene` 没有归属列，
    判据按**成员记忆**反查（同 `read_offload_file` 对 `MemoryItem` 的过渡
    推导）。场景是聚类产物，成员可能分属多个用户，所以口径是
    「**任一成员可见即可见**」：

    - admin/system：不校验（与 acl.py 的 admin/system 全权同口径）
    - 非 admin（**含 `principal=None`**，见下）：一个可见成员都没有 →
      `ValueError("scene not found")`，**不区分"没有"与"不是你的"**
      （同 `get_scratchpad` 口径——区分本身就是信息泄漏：能据此探知
      某 scene_id 是否存在）

    **`principal=None` 不再是"不校验"（票 `.scratch/mcp-identity-gaps/09`）**：
    MCP `scene_get` / `scenes_list` 的宿主不透传 `user_id` 时正是 None
    （`mcp.py:132` 的 `_principal_from_params` 返回 None），而
    `principal is not None` 这个判据让 None 整段跳过归属——实测
    （`.scratch/mcp-identity-gaps/probe_scene_none_semantics.py`）：
    None 调 `get_scene('sc-B')` 拿到 B 的 summary **和全部成员正文**；
    None 调 `list_scenes` 把 B 的场景连摘要一起列出来。
    改为经 `acl.viewer_of` 收敛（None → `"default"`）后照常校验，
    与票 07 给 `build_memories_page`、04 号票给 `checkpoint_write`
    同一个真源，不发明第二份口径。

    **单人部署不空转**：真实库唯一非空属主就是 `default`，收敛后
    照样看得见自己的全部场景（探针 S3/S4 实测带身份收窄正确，
    admin 仍全权）。

    **`visible` 既是闸门也是过滤器**（:282 判空 + :288 按 id 过滤成员）：
    纯 B 的簇对 A 直接不存在；混合簇只返回 A 可见的成员。
    docstring 早年写的"混了 B 的成员的簇仍会把 B 的正文带出来"
    是 :288 补上**之前**的说法，已过期——探针 S5 实测 A 只见 m-mix-A。
    """
    with db.get_session() as s:
        scene = s.get(MemoryScene, scene_id)
        if not scene:
            raise ValueError("scene not found")
        visible: set[str] | None = None
        if not bool(getattr(principal, "is_admin", False)):
            visible = _scene_visible_member_ids(s, scene_id, principal)
            if not visible:
                raise ValueError("scene not found")
        members = s.exec(
            select(MemoryItem)
            .where(MemoryItem.scene_id == scene_id, MemoryItem.status == "active")
            .order_by(MemoryItem.use_count.desc())
        ).all()
        if visible is not None:
            members = [m for m in members if m.id in visible]
        return {
            "scene": {
                "id": scene.id,
                "name": scene.name,
                "summary": scene.summary,
                "heat": scene.heat,
                "member_count": scene.member_count,
                "updated_at": scene.updated_at.isoformat(),
            },
            "members": [
                {
                    "id": m.id,
                    "key": m.key,
                    "content": m.content,
                    "lane": m.lane,
                    "use_count": m.use_count,
                    "decay_class": m.decay_class,
                }
                for m in members
            ],
        }


def list_scenes(limit: int = 50, principal=None) -> dict:
    """场景列表（heat 降序，heat 并列按成员数降序）。

    归属（票 `.scratch/mcp-identity-gaps/01b`）：`summary` 是 LLM 依据成员
    内容生成的摘要——A 刷列表就读到 B 的场景主题。判据同 `get_scene`：
    至少一个可见成员才列（一条 SQL 取全部，避免逐场景 N+1）。
    `member_count` 仍报**场景全量成员数**（场景自身属性，非归属信息）。

    **`principal=None` 收敛到 `"default"` 而不是不校验**（票
    `.scratch/mcp-identity-gaps/09`）：MCP `scenes_list` 宿主不透传
    `user_id` 时正是 None，而 `scoped = principal is not None and ...`
    让 None 时整段跳过——实测 None 调 `list_scenes` 把 B 的场景连
    LLM 摘要一起列出来（`probe_scene_none_semantics.py` S2）。
    改法同 `get_scene`：经 `acl.viewer_of` 收敛（None → `"default"`）。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    # 注意：这里是 `not is_admin` 而不是 `principal is not None and not is_admin`
    # ——`getattr(None, "is_admin", False)` 返回 False，所以 None 照样进收窄分支，
    # 再由 viewer_of 收敛到 "default"（票 09）。
    scoped = not bool(getattr(principal, "is_admin", False))
    with db.get_session() as s:
        scenes = s.exec(
            select(MemoryScene).order_by(MemoryScene.heat.desc(), MemoryScene.member_count.desc())
        ).all()
        if scoped:
            from lantai.core.acl import viewer_of

            viewer = viewer_of(principal)
            visible_scene_ids = set(
                s.exec(
                    select(MemoryItem.scene_id)
                    .where(
                        MemoryItem.status == "active",
                        MemoryItem.scene_id.is_not(None),
                        (MemoryItem.user_id == viewer) | MemoryItem.user_id.is_(None),
                    )
                    .distinct()
                ).all()
            )
            scenes = [sc for sc in scenes if sc.id in visible_scene_ids]
        return {
            "scenes": [
                {
                    "id": sc.id,
                    "name": sc.name,
                    "summary": sc.summary,
                    "heat": sc.heat,
                    "member_count": sc.member_count,
                }
                for sc in scenes[:limit]
            ]
        }


def format_scene_block(scene: dict, members: list, max_chars: int, suffix: str) -> tuple[str, str]:
    """单个场景导航块（纯函数）：## Scene: 名称（热度 N，成员 M）+ 摘要 + 成员 key。

    返回 (注入行, evidence 内容)——evidence 与注入行同源。
    """
    name = (scene.get("name") or "未命名场景").strip()
    heat = scene.get("heat", 0)
    member_count = scene.get("member_count", len(members))
    block = [f"## Scene: {name}（热度 {heat}，成员 {member_count}）"]
    summary = (scene.get("summary") or "").strip()
    if summary:
        block.append(f"- 摘要: {summary}")
    keys = [m.key for m in members if m.key]
    shown = keys[: settings.SCENE_MAX_MEMBERS_SHOWN]
    if shown:
        block.append("- 成员: " + "、".join(shown))
    text = "\n".join(block)
    truncated = truncate_codepoints(text, max_chars, suffix)
    return truncated, truncated


def scene_navigation(
    scene_blocks: list[tuple[str, str]], max_total_chars: int
) -> tuple[list[str], int]:
    """总预算内按序装入导航块（复用 apply_recall_budget），返回 (lines, dropped)。"""
    lines = [b[0] for b in scene_blocks]
    return apply_recall_budget(lines, max_total_chars)


def build_scene_navigation_lines(items: list, per_scene_chars: int, suffix: str) -> list[str]:
    """命中记忆按场景分组 → 场景导航块列表（heat 降序）。

    渐进式披露：只给导航（场景名 + 摘要 + 成员 key），详情走 scene_get 下钻。
    异常/无场景零侵入降级为空列表。
    """
    scene_ids = {m.scene_id for m in items if m.scene_id}
    if not scene_ids:
        return []
    try:
        with db.get_session() as s:
            scenes = s.exec(select(MemoryScene).where(MemoryScene.id.in_(scene_ids))).all()
        scene_map = {sc.id: sc for sc in scenes}
    except Exception:
        return []
    groups: dict[str, list] = {}
    for m in items:
        if m.scene_id in scene_map:
            groups.setdefault(m.scene_id, []).append(m)
    if not groups:
        return []
    ordered = sorted(
        groups.items(),
        key=lambda kv: (scene_map[kv[0]].heat, scene_map[kv[0]].member_count),
        reverse=True,
    )
    lines = []
    for sid, members in ordered:
        sc = scene_map[sid]
        line, _content = format_scene_block(
            {
                "name": sc.name,
                "summary": sc.summary,
                "heat": sc.heat,
                "member_count": sc.member_count,
            },
            members,
            per_scene_chars,
            suffix,
        )
        lines.append(line)
    return lines
