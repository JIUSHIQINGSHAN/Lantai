from sqlmodel import select
from lantai.core.settings import settings
from lantai.core.ids import new_id
from lantai.models.enums import GateDecision
from lantai.models.tables import MemoryCandidate, MemoryItem, ConflictEvent
from lantai.storage import db
from lantai.gate.scorer import novelty_score
from lantai.gate.contradiction import check_contradiction
from lantai.gate.conflict_rules import check_rules


def decide(candidate_id: str) -> dict:
    with db.get_session() as s:
        cand = s.get(MemoryCandidate, candidate_id)
        if not cand:
            return {"decision": GateDecision.REJECT, "reason": "candidate not found"}

        if cand.extractor_confidence < settings.GATE_MIN_EXTRACTOR_CONF:
            return {"decision": GateDecision.REJECT,
                    "reason": f"low extractor confidence {cand.extractor_confidence:.2f}"}

        related = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
        related_texts = [m.content for m in related][:50]

        summary_text = cand.summary or " ".join(cand.claims)[:400]
        nv = novelty_score(summary_text, related_texts) if related_texts else 1.0

        conflicts = []
        # P0-2：确定性规则层优先（零 LLM、可复现）；命中写账本 ConflictEvent
        for m in related[:10]:
            hits = check_rules(summary_text, m.content)
            for hit in hits:
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
                    kind="mutex",
                    detail={"new_matched": hit["new_matched"],
                            "old_matched": hit["old_matched"]},
                    status="open",
                ))
        # 规则未命中 → 回落 LLM 矛盾检测（降级不阻断）
        if not conflicts:
            for m in related[:10]:
                c = check_contradiction(summary_text, m.content)
                if c.get("contradicts"):
                    conflicts.append({"memory_id": m.id, "severity": c.get("severity", "low"),
                                      "reason": c.get("reason", "")})

        if conflicts and any(c["severity"] == "high" for c in conflicts):
            s.commit()  # 账本落库
            return {"decision": GateDecision.ARCHIVE_CONFLICT,
                    "reason": "hard contradiction with existing memory",
                    "conflicts": conflicts, "novelty": nv}

        if nv < 0.15:
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
