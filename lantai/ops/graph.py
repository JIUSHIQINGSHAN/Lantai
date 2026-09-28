"""记忆关系图（v0.9 MAP 星图数据源，借鉴 aiduMEI MAP 面板窄版）。

零依赖只读聚合：把「哪些记忆之间有明确关系」画成一张图。
- 记忆节点：active MemoryItem；只有参与 MemoryEdge 或属于某个 scene 的记忆才入选
  （孤立记忆不上图，避免噪点）。
- 来源节点：参与边的 RawDocument（doc_*，来源文档 -> 记忆 的支撑/取代关系），
  带 title/url，面板外环展示——出处可溯。
- 链接：MemoryEdge（source/target/relation/confidence），两端节点都在入选集合才保留
  （跨池边、指向 archived/池外记忆的边丢弃）。

build_graph(session, limit) 是纯函数（测试直传临时 session，不 mock 内部逻辑）；
get_graph(limit) 打开默认会话执行。
"""

from collections import Counter
from datetime import UTC

from sqlmodel import Session, or_, select

from lantai.models.tables import MemoryEdge, MemoryItem, MemoryScene, RawDocument
from lantai.storage import db

LABEL_MAX = 48


def validate_graph_limit(limit) -> int:
    """limit 校验（REST/MCP/纯函数共用）：[1,500] 的 int，否则抛 ValueError。

    宁 miss 不脏写：非法值不静默修正，由调用方决定 422/拒绝。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    return limit


def _label(item: MemoryItem) -> str:
    """节点短标签：内容首行截断（与 _label_from_text 同形），空则回退 key。"""
    return _label_from_text(item.content or "") or item.key


def build_graph(session: Session, limit: int = 150, principal=None) -> dict:
    """只读聚合：给定 session 构建记忆关系图（节点 + 链接 + 统计）。

    - 记忆候选池：active MemoryItem 按 updated_at 降序取 limit 条；
    - 来源节点：参与池内边的 RawDocument（doc_* 来源文档）；
    - 节点入选：参与入选边任一端，或携带 scene_id（scene 聚簇）；
    - 链接保留：source/target 均为入选节点（跨池边、archived/池外端点丢弃）。
    """
    validate_graph_limit(limit)

    # 归属（票 `.scratch/mcp-identity-gaps/12`）：此前这里是
    # `build_memories_page` **修前**的同族形状——`if principal:` 块内只判
    # `user_id` 非空就加归属条件，**没有 `is_admin` 豁免、没有 `OR IS NULL`、
    # 没有 None 收敛**。票 03 / 07 / 08 三次修正都没跟上这里，三个后果：
    #   · `principal=None`（MCP 宿主不透传 user_id）→ 一个条件都不加 → **星图
    #     露出别人的记忆节点**（实测 D 形态 nodes=2 含 user-B）；
    #   · 显式 user → 只留自己的行，跨用户边另一端不在池 → 边整个被丢 →
    #     **自己的图也空白**；NULL 属主老边同时断掉（老边人人可读是既定口径）；
    #   · admin（`user_id="api_key"`，`auth.py:168` 的真形态）→ 被收窄到
    #     `user_id=='api_key'`，真实库没这个属主 → **星图对管理员空白**。
    # 修法与仓内多数派逐字一致（`build_memories_page`、`get_core_memory`、
    # `find_duplicate_verbatim`、`hybrid_search` 均如此），不新造第四种口径。
    stmt = (
        select(MemoryItem)
        .where(MemoryItem.status == "active")
        .order_by(MemoryItem.updated_at.desc())
    )
    if principal is None or bool(getattr(principal, "is_admin", False)):
        # 无身份 → 收敛到 `viewer_of(None)`（"default"）+ NULL 老行；
        # admin → 一律不加归属过滤（同票 08）。两条都走同一形状：
        # 收窄到 `user_id == viewer OR IS NULL`，viewer 对 admin 无意义
        # 故提前分支，不合成一个条件。
        if principal is None:
            from lantai.core.acl import viewer_of

            stmt = stmt.where(
                (MemoryItem.user_id == viewer_of(None)) | (MemoryItem.user_id.is_(None))
            )
    else:
        if getattr(principal, "tenant_id", None):
            stmt = stmt.where(MemoryItem.tenant_id == principal.tenant_id)
        if getattr(principal, "user_id", None):
            from lantai.core.acl import viewer_of

            # `OR IS NULL`（票 03 口径）：NULL 属主老行人人可读，老边不能断。
            stmt = stmt.where(
                (MemoryItem.user_id == viewer_of(principal)) | (MemoryItem.user_id.is_(None))
            )
        if getattr(principal, "session_id", None):
            stmt = stmt.where(MemoryItem.session_id == principal.session_id)
        if getattr(principal, "agent_id", None):
            stmt = stmt.where(MemoryItem.agent_id == principal.agent_id)
        if principal.allowed_lanes is not None:
            from sqlmodel import col

            stmt = stmt.where(col(MemoryItem.lane).in_(principal.allowed_lanes))

    stmt = stmt.limit(limit)
    memories = session.exec(stmt).all()
    mem_ids = {m.id for m in memories}
    if not mem_ids:
        return _empty_graph()

    # 池内边：只取任一端在候选池里的边，避免全表扫
    edges = session.exec(
        select(MemoryEdge).where(
            or_(
                MemoryEdge.source_memory_id.in_(mem_ids),
                MemoryEdge.target_memory_id.in_(mem_ids),
            )
        )
    ).all()

    # 来源节点候选：池内边的另一端（非记忆端点，如 doc_* RawDocument）。
    # 只收「另一端在池内」的端点——archived/池外记忆端点不会成为来源节点。
    doc_ids = set()
    for e in edges:
        if e.source_memory_id in mem_ids and e.target_memory_id not in mem_ids:
            doc_ids.add(e.target_memory_id)
        elif e.target_memory_id in mem_ids and e.source_memory_id not in mem_ids:
            doc_ids.add(e.source_memory_id)
    docs = session.exec(select(RawDocument).where(RawDocument.id.in_(doc_ids))).all()
    doc_map = {d.id: d for d in docs}
    allowed = mem_ids | set(doc_map)

    links: list[dict] = []
    seen: set[tuple] = set()
    for e in edges:
        if e.source_memory_id not in allowed or e.target_memory_id not in allowed:
            continue
        key = (e.source_memory_id, e.target_memory_id, e.relation)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "source": e.source_memory_id,
                "target": e.target_memory_id,
                "relation": e.relation,
                "confidence": round(float(e.confidence or 0.0), 3),
            }
        )

    linked_ids = {l["source"] for l in links} | {l["target"] for l in links}
    nodes: list[dict] = []
    for m in memories:
        if m.id not in linked_ids and not m.scene_id:
            continue
        nodes.append(
            {
                "id": m.id,
                "node_type": "memory",
                "label": _label(m),
                "lane": m.lane,
                "decay_class": m.decay_class,
                "scene_id": m.scene_id,
            }
        )
    for did, d in doc_map.items():
        if did not in linked_ids:
            continue
        nodes.append(
            {
                "id": did,
                "node_type": "source",
                "label": _doc_label(d),
                "lane": None,
                "decay_class": None,
                "scene_id": None,
                "url": d.url or None,
            }
        )

    # scene 名称映射（供前端聚簇标注）
    scene_ids = {n["scene_id"] for n in nodes if n["scene_id"]}
    scenes: dict[str, str] = {}
    if scene_ids:
        for sc in session.exec(select(MemoryScene).where(MemoryScene.id.in_(scene_ids))).all():
            scenes[sc.id] = sc.name

    return {
        "generated_at": _now_iso(),
        "nodes": nodes,
        "links": links,
        "scenes": scenes,
        "stats": {
            "lane_counts": {
                k: int(v)
                for k, v in Counter(n["lane"] for n in nodes if n["node_type"] == "memory").items()
            },
            "node_type_counts": {
                k: int(v) for k, v in Counter(n["node_type"] for n in nodes).items()
            },
            "edge_counts": {k: int(v) for k, v in Counter(l["relation"] for l in links).items()},
        },
    }


def get_graph(limit: int = 150, principal=None) -> dict:
    """打开默认会话执行（只读）。"""
    validate_graph_limit(limit)
    with db.get_session() as s:
        return build_graph(s, limit, principal=principal)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _doc_label(doc) -> str:
    """来源节点短标签：title 优先，空则内容首行截断。"""
    title = (doc.title or "").strip()
    if title:
        return title if len(title) <= LABEL_MAX else title[:LABEL_MAX] + "…"
    return _label_from_text(doc.content or "")


def _label_from_text(content: str) -> str:
    content = content.strip().replace("\n", " ")
    if len(content) > LABEL_MAX:
        return content[:LABEL_MAX] + "…"
    return content


def _empty_graph() -> dict:
    return {
        "generated_at": _now_iso(),
        "nodes": [],
        "links": [],
        "scenes": {},
        "stats": {"lane_counts": {}, "node_type_counts": {}, "edge_counts": {}},
    }
