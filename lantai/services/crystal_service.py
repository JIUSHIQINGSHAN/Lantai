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
        candidates.append({
            "skill_name": skill_name,
            "trigger_rule": f"当出现与「{topic}」相关的连续需求或重复操作时触发",
            "procedure": "\n".join(
                f"- {m.content[:80]}" for m in cluster[:5]),
            "source_lanes": sorted({m.lane for m in cluster}),
            "sample_keys": sorted({m.key or m.content[:20] for m in cluster})[:10],
            "candidate_count": len(cluster),
        })
    return candidates


def run_crystal_detect_once(namespace: str = "default", *,
                            dry_run: bool = True,
                            limit: int | None = None) -> dict:
    """执行一轮结晶检测：聚类 -> 候选（dry_run 不写库）。

    返回 {"clusters", "candidates", "created", "updated", "skipped"}；
    候选按 skill_name upsert：存在则 hit_count+1（幂等，不重复堆积）。
    """
    if not settings.CRYSTAL_ENABLED:
        return {"clusters": 0, "candidates": 0, "created": 0, "updated": 0,
                "skipped": ["CRYSTAL_ENABLED=false"]}
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
        with db.get_session() as s:
            for cand in candidates[:settings.CRYSTAL_MAX_DAILY]:
                existing = s.exec(select(SkillCrystal).where(
                    SkillCrystal.skill_name == cand["skill_name"])).first()
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
                    s.add(SkillCrystal(id=new_id("crystal"), **cand))
                    created += 1
            s.commit()
    return {"clusters": len(clusters), "candidates": len(candidates),
            "created": created, "updated": updated, "skipped": []}


def list_crystals(status: str = "candidate", limit: int = 50) -> dict:
    """列出结晶候选项（默认 candidate 待审）。"""
    with db.get_session() as s:
        rows = s.exec(select(SkillCrystal).where(
            SkillCrystal.status == status
        ).order_by(SkillCrystal.updated_at.desc()).limit(limit)).all()
        return {"crystals": [r.model_dump(mode="json") for r in rows]}


def decide_crystal(crystal_id: str, approve: bool,
                   steps: list[str] | None = None,
                   reason: str = "") -> dict:
    """裁决候选：approve 必须带非空 steps -> 落成 Skill 资产 + approved；
    reject -> archived + reason（宁 miss 不脏写）。"""
    with db.get_session() as s:
        crystal = s.get(SkillCrystal, crystal_id)
        if not crystal:
            raise ValueError("crystal not found")
        skill_name, trigger_rule = crystal.skill_name, crystal.trigger_rule
    result = None
    if approve:
        steps = [str(x).strip() for x in (steps or []) if str(x).strip()]
        if not steps:
            raise ValueError("approve requires non-empty steps (宁 miss 不脏写)")
        from lantai.services.mem_command import create_skill
        result = create_skill(name=skill_name, description=trigger_rule, steps=steps)
        if not result.get("ok"):
            raise ValueError(result.get("error", "create_skill failed"))
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
    return {"ok": True, "crystal_id": crystal_id,
            "skill": result if approve else None}
