from remembrance.core.time import utcnow
from remembrance.models.tables import MemoryItem, MemoryUsageFeedback
from remembrance.storage import db
from remembrance.core.ids import new_id


def record_feedback(memory_id: str, query: str,
                    helped: bool, user_accepted: bool,
                    hallucination_risk: float) -> dict:
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False}
        delta = (0.1 if helped else -0.05) + (0.1 if user_accepted else 0) \
                - 0.2 * hallucination_risk
        mem.use_count += 1
        mem.helpful_count += int(helped)
        mem.importance = max(0.0, min(1.0, mem.importance + delta))
        mem.last_used_at = utcnow()
        s.add(mem)
        s.add(MemoryUsageFeedback(
            id=new_id("fb"), memory_id=memory_id, query=query,
            helped=helped, user_accepted=user_accepted,
            hallucination_risk=hallucination_risk, score_delta=delta,
        ))
        s.commit()
        return {"ok": True, "importance": mem.importance}
