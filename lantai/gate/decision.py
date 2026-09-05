from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.evolution.promoter import _make_checkpoint
from lantai.gate.conflict_rules import check_antonyms, check_negation_pairs, check_rules
from lantai.gate.contradiction import check_contradiction
from lantai.gate.scorer import novelty_score
from lantai.llm.client import embed
from lantai.models.enums import GateDecision
from lantai.models.tables import ConflictEvent, MemoryCandidate, MemoryItem
from lantai.storage import db
from lantai.storage.vector_store import get_vector_store


def _load_conflict_candidates(s, summary_text: str) -> list[MemoryItem]:
    """按向量相似度召回冲突检测候选（DD-03 修复：替代全表 [:10] 插入序抽查）。

    降级策略：向量检索失败时回退到全表前 CONFLICT_CHECK_TOP_K 条（宁有偏 miss 不全漏）。
    """
    top_k = settings.CONFLICT_CHECK_TOP_K
    try:
        qv = embed([summary_text])[0]
        vs = get_vector_store()
        vec_results = vs.search(qv, top_k=top_k)
        if vec_results:
            near_ids = [r["id"] for r in vec_results]
            candidates = s.exec(
                select(MemoryItem)
                .where(MemoryItem.id.in_(near_ids), MemoryItem.status == "active")
            ).all()
            if candidates:
                return candidates
    except Exception as e:
        logger.warning("conflict vector recall failed, falling back to rowid order: %s", e)

    # 降级：向量不可用时按 rowid 取前 top_k（行为与修复前一致但可配置）
    return s.exec(
        select(MemoryItem)
        .where(MemoryItem.status == "active")
        .limit(top_k)
    ).all()


def decide(candidate_id: str) -> dict:
    with db.get_session() as s:
        cand = s.get(MemoryCandidate, candidate_id)
        if not cand:
            return {"decision": GateDecision.REJECT, "reason": "candidate not found"}

        if cand.extractor_confidence < settings.GATE_MIN_EXTRACTOR_CONF:
            return {"decision": GateDecision.REJECT,
                    "reason": f"low extractor confidence {cand.extractor_confidence:.2f}"}

        summary_text = cand.summary or " ".join(cand.claims)[:400]

        # DD-03: 用向量召回语义近邻作为冲突检测候选
        related = _load_conflict_candidates(s, summary_text)
        related_texts = [m.content for m in related][:settings.GATE_NOVELTY_SAMPLE_SIZE]

        nv = novelty_score(summary_text, related_texts) if related_texts else 1.0

        conflicts = []   # 硬冲突（高 salience 确定性 + LLM）→ archive_conflict
        demoted = []     # 低 salience 确定性冲突 → 已降权放行（ADR-0020）
        demote_t = settings.CONFLICT_SALIENCE_MIN_IMPORTANCE
        demote_step = settings.CONFLICT_SALIENCE_DEMOTE_STEP
        # ADR-0020：确定性规则 + 反义词碰撞双通道优先（零 LLM、可复现）
        for m in related:
            hits = check_rules(summary_text, m.content) \
                + check_antonyms(summary_text, m.content)
            for hit in hits:
                if m.importance < demote_t:
                    # salience 降权：弱旧记忆不挡新信息——降权（可回滚）+ 账本 resolved + 放行
                    old_imp = m.importance
                    m.importance = max(0.0, old_imp - demote_step)
                    _make_checkpoint(s, m, {"importance": old_imp}, "",
                                     trigger="salience_demote")
                    s.add(ConflictEvent(
                        id=new_id("cfev"),
                        memory_id=m.id,
                        incoming_ref=summary_text[:200],
                        rule_name=hit["rule_name"],
                        kind="salience_demote",
                        detail={"new_matched": hit.get("new_matched"),
                                "old_matched": hit.get("old_matched"),
                                "demoted_from": round(old_imp, 4)},
                        status="resolved",
                    ))
                    demoted.append({"memory_id": m.id,
                                    "reason": f"salience demote '{hit['rule_name']}'"})
                else:
                    conflicts.append({
                        "memory_id": m.id,
                        "severity": "high",
                        "reason": (f"deterministic rule '{hit['rule_name']}' "
                                   f"(new '{hit['new_matched']}' vs old '{hit['old_matched']}')"),
                        "rule_name": hit["rule_name"],
                    })
                    s.add(ConflictEvent(
                        id=new_id("cfev"),
                        memory_id=m.id,
                        incoming_ref=summary_text[:200],
                        rule_name=hit["rule_name"],
                        kind=hit.get("kind", "mutex"),
                        detail={"new_matched": hit["new_matched"],
                                "old_matched": hit["old_matched"]},
                        status="open",
                    ))
        # 规则/反义词均未命中（且无降权动作）→ 回落 LLM 矛盾检测（降级不阻断）
        if not conflicts and not demoted:
            for m in related:
                try:
                    c = check_contradiction(summary_text, m.content)
                except Exception:
                    c = {"contradicts": False, "reason": "", "severity": "low"}
                if c.get("contradicts"):
                    conflicts.append({"memory_id": m.id, "severity": c.get("severity", "low"),
                                      "reason": c.get("reason", "")})

        # ADR-0024：单字否定对候选（是/不是、会/不会…）→ LLM 裁决。
        # 候选不落硬规则；LLM 判非矛盾/失败 → 放行（宁 miss）。
        if settings.CONFLICT_NEGATION_ENABLED:
            for m in related:
                if check_negation_pairs(summary_text, m.content):
                    try:
                        c = check_contradiction(summary_text, m.content)
                    except Exception:
                        c = {"contradicts": False, "reason": "", "severity": "low"}
                    if c.get("contradicts"):
                        conflicts.append({
                            "memory_id": m.id,
                            "severity": c.get("severity", "low"),
                            "reason": f"negation candidate: {c.get('reason', '')}",
                        })

        if demoted:
            s.commit()  # 降权 + Checkpoint + resolved 账本持久化（宁 miss 不脏写：有迹可溯）

        if conflicts and any(c["severity"] == "high" for c in conflicts):
            s.commit()  # 账本落库
            return {"decision": GateDecision.ARCHIVE_CONFLICT,
                    "reason": "hard contradiction with existing memory",
                    "conflicts": conflicts, "novelty": nv}

        if nv < settings.GATE_NOVELTY_THRESHOLD:
            # 语义高度重叠 ≠ 丢弃：可能有增量信息（如新配置项）。
            # 降级到 WORKING_ONLY，由提案系统走 update/merge 并入现有记忆，
            # 而不是静默 reject 丢数据（落地实战发现：显卡 RTX3050 增量被误杀）。
            return {"decision": GateDecision.WORKING_ONLY,
                    "reason": f"low novelty {nv:.2f}, fallback to merge path",
                    "novelty": nv, "conflicts": conflicts}

        if cand.actions:
            return {"decision": GateDecision.PROMOTE_PROCEDURAL,
                    "novelty": nv, "conflicts": conflicts}

        return {"decision": GateDecision.WORKING_ONLY,
                "novelty": nv, "conflicts": conflicts}
