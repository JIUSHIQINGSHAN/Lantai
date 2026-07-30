from sqlmodel import select
from remembrance.core.settings import settings
from remembrance.models.enums import GateDecision
from remembrance.models.tables import MemoryCandidate, MemoryItem
from remembrance.storage import db
from remembrance.gate.scorer import novelty_score
from remembrance.gate.contradiction import check_contradiction


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
        for m in related[:10]:
            c = check_contradiction(summary_text, m.content)
            if c.get("contradicts"):
                conflicts.append({"memory_id": m.id, "severity": c.get("severity", "low"),
                                  "reason": c.get("reason", "")})

        if conflicts and any(c["severity"] == "high" for c in conflicts):
            return {"decision": GateDecision.ARCHIVE_CONFLICT,
                    "reason": "hard contradiction with existing memory",
                    "conflicts": conflicts, "novelty": nv}

        if nv < 0.15:
            return {"decision": GateDecision.REJECT,
                    "reason": f"redundant, novelty={nv:.2f}"}

        if cand.actions:
            return {"decision": GateDecision.PROMOTE_PROCEDURAL,
                    "novelty": nv, "conflicts": conflicts}

        return {"decision": GateDecision.WORKING_ONLY,
                "novelty": nv, "conflicts": conflicts}
