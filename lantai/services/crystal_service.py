"""技能结晶服务（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）。

Mímir 铁律：LLM/规则只能建议，不能直接 commit——检测只产 candidate 候选项，
人工审核（decide approve 必须带非空 steps）后才落成 Skill 资产；
宁 miss 不脏写：低质量簇不产候选、缺 steps 不批准、噪声 lane 排除。
检测聚类复用 autodream.cluster_memories（同 lane + 共享关键词，确定性可复现）。
"""

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.evolution.autodream import _keywords, cluster_memories
from lantai.models.tables import MemoryItem, SkillCrystal
from lantai.storage import db

# 噪声 lane：碎片化/闲聊不适合结晶为技能（对照作者排除 general/uncategorized 等）
_NOISE_LANES = {"general", "chat"}


def build_crystal_candidates(clusters: list[list]) -> list[dict]:
    """簇 -> 候选形状（纯函数，零 DB 零 LLM）。

    procedure 只记内容摘要不塞全文（对照作者 v17 修复：procedure 聚焦步骤摘要
    而非原始记忆拼接）；skill_name 幂等唯一（重复检测按名 upsert）。
    """
    candidates: list[dict] = []
    for cluster in clusters:
        lane = cluster[0].lane
        kws = sorted({w for m in cluster for w in _keywords(m.content)})
        topic = kws[0] if kws else (cluster[0].key or "topic")
        skill_name = f"crystallized-{lane}-{topic}"[:80]
        candidates.append(
            {
                "skill_name": skill_name,
                "trigger_rule": f"当出现与「{topic}」相关的连续需求或重复操作时触发",
                "procedure": "\n".join(f"- {m.content[:80]}" for m in cluster[:5]),
                "source_lanes": sorted({m.lane for m in cluster}),
                "sample_keys": sorted({m.key or m.content[:20] for m in cluster})[:10],
                "candidate_count": len(cluster),
            }
        )
    return candidates


def run_crystal_detect_once(
    namespace: str = "default",
    *,
    dry_run: bool = True,
    limit: int | None = None,
    principal=None,
) -> dict:
    """执行一轮结晶检测：聚类 -> 候选（dry_run 不写库）。

    返回 {"clusters", "candidates", "created", "updated", "skipped"}；
    候选按 skill_name upsert：存在则 hit_count+1（幂等，不重复堆积）。

    归属（票 11）：新建的候选落 `principal.user_id`，否则结晶又是
    一条谁都能看见的无主行。`principal=None` 的后台巡检留 NULL。
    """
    if not settings.CRYSTAL_ENABLED:
        return {
            "clusters": 0,
            "candidates": 0,
            "created": 0,
            "updated": 0,
            "skipped": ["CRYSTAL_ENABLED=false"],
        }
    with db.get_session() as s:
        q = select(MemoryItem).where(
            MemoryItem.status == "active",
            MemoryItem.namespace == namespace,
            MemoryItem.memory_type != "skill",
            MemoryItem.lane.not_in(_NOISE_LANES),
        )
        if limit:
            q = q.limit(limit)
        items = s.exec(q).all()
    clusters = cluster_memories(items, min_size=settings.CRYSTAL_MIN_CLUSTER)
    candidates = build_crystal_candidates(clusters)
    created = updated = 0
    if not dry_run:
        owner = getattr(principal, "user_id", None)
        tenant = getattr(principal, "tenant_id", None)
        with db.get_session() as s:
            for cand in candidates[: settings.CRYSTAL_MAX_DAILY]:
                existing = s.exec(
                    select(SkillCrystal).where(SkillCrystal.skill_name == cand["skill_name"])
                ).first()
                if existing:
                    existing.hit_count += 1
                    existing.candidate_count = cand["candidate_count"]
                    existing.procedure = cand["procedure"]
                    existing.sample_keys = cand["sample_keys"]
                    existing.source_lanes = cand["source_lanes"]
                    existing.updated_at = utcnow()
                    s.add(existing)
                    updated += 1
                else:
                    s.add(
                        SkillCrystal(
                            id=new_id("crystal"),
                            user_id=owner,
                            tenant_id=tenant,
                            **cand,
                        )
                    )
                    created += 1
            s.commit()
    return {
        "clusters": len(clusters),
        "candidates": len(candidates),
        "created": created,
        "updated": updated,
        "skipped": [],
    }


def _crystal_scope(principal):
    """读侧归属条件：admin/系统身份 → None（不过滤）；
    否则 `user_id == viewer OR user_id IS NULL`（票 11，NULL 口径同票 03/04/06/09）。

    **`principal=None` 收敛到 `"default"`，不再返回 None**（票
    `.scratch/mcp-identity-gaps/06`）：旧 docstring 声称 None 是"后台巡检"，
    但 grep 实证 `list_crystals` 的调用方只有 `routes_crystals.py:21`（HTTP）
    与 `mcp.py:752`（MCP），**没有 worker/scheduler**——`crystals_detect`
    走的是另一条路。旧口径让无身份的 MCP 调用列出全库结晶
    （探针实测返回 `cry-B`）。系统批处理要全表显式传 `acl.SYSTEM_VIEWER`。
    """
    from lantai.core.acl import SYSTEM_VIEWER
    from lantai.services.work_item_service import _viewer_of

    if bool(getattr(principal, "is_admin", False)):
        return None
    viewer = _viewer_of(principal)
    if viewer == SYSTEM_VIEWER:
        return None
    return (SkillCrystal.user_id == viewer) | (SkillCrystal.user_id.is_(None))


def list_crystals(status: str = "candidate", limit: int = 50, principal=None) -> dict:
    """列出结晶候选项（默认 candidate 待审）。

    归属（票 .scratch/readside-gaps/11）：`procedure` / `trigger_rule`
    是从记忆里蒸馏出的操作流程，原本一个身份都不取、全文可读。
    """
    with db.get_session() as s:
        q = select(SkillCrystal).where(SkillCrystal.status == status)
        scope = _crystal_scope(principal)
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q.order_by(SkillCrystal.updated_at.desc()).limit(limit)).all()
        return {"crystals": [r.model_dump(mode="json") for r in rows]}


def decide_crystal(
    crystal_id: str,
    approve: bool,
    steps: list[str] | None = None,
    reason: str = "",
    principal=None,
) -> dict:
    """裁决候选：approve 必须带非空 steps -> 落成 Skill 资产 + approved；
    reject -> archived + reason（宁 miss 不脏写）。

    归属（票 11）：裁决是破坏性操作——A 不能替 B 批准/驳回一个技能结晶。
    校验在任何写操作**之前**（403 不能伴随落库），复用
    `acl.ensure_can_delete` 单一真源（同票 02）。
    """
    with db.get_session() as s:
        crystal = s.get(SkillCrystal, crystal_id)
        if not crystal:
            raise ValueError("crystal not found")
        if crystal.status != "candidate":
            raise ValueError("crystal state changed; refresh and retry")
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=crystal.user_id,
                resource_tenant_id=crystal.tenant_id,
            )
        skill_name, trigger_rule = crystal.skill_name, crystal.trigger_rule
    result = None
    if approve:
        steps = [str(x).strip() for x in (steps or []) if str(x).strip()]
        if not steps:
            raise ValueError("approve requires non-empty steps (宁 miss 不脏写)")
        from lantai.services.mem_command import create_skill

        # 归属（票 .scratch/mcp-identity-gaps/01b）：create_skill 补了
        # principal 形参后，这里要把裁决者的身份递下去——否则 MCP
        # `crystal_decide` 落的技能仍是 user_id=NULL，读侧按 viewer 收窄
        # 时谁都检索不到（沉淀了却看不见）。HTTP 侧原本就带 ctx，顺带受益。
        result = create_skill(
            name=skill_name, description=trigger_rule, steps=steps, principal=principal
        )
        if not result.get("ok"):
            raise ValueError(result.get("error", "create_skill failed"))
    elif not (reason or "").strip():
        raise ValueError("reject reason is required")
    with db.get_session() as s:
        crystal = s.get(SkillCrystal, crystal_id)
        if crystal:
            if approve:
                crystal.status = "approved"
            else:
                crystal.status = "archived"
                crystal.decision_reason = reason or ""
            crystal.updated_at = utcnow()
            s.add(crystal)
            s.commit()
    return {"ok": True, "crystal_id": crystal_id, "skill": result if approve else None}
