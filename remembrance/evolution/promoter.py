from sqlmodel import select
from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.core.settings import settings
from remembrance.llm.client import embed
from remembrance.retrieval.hybrid import index_memory_item, delete_memory_item
from remembrance.models.enums import ProposalStatus, MemoryTier
from remembrance.models.tables import (MemoryProposal, MemoryItem, MemoryCheckpoint)
from remembrance.storage import db


def _make_checkpoint(session, mem: MemoryItem, before: dict,
                     proposal_id: str, trigger: str):
    session.add(MemoryCheckpoint(
        id=new_id("ckpt"),
        memory_id=mem.id, version=mem.version,
        before=before, after=mem.model_dump(mode="json"),
        proposal_id=proposal_id, trigger=trigger,
    ))


def apply_proposal(proposal_id: str) -> dict:
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        if not prop or prop.status != ProposalStatus.PENDING:
            return {"ok": False, "reason": "not applicable"}

        patch = prop.proposed_patch
        mem_type = patch.get("memory_type", "semantic")
        key = patch.get("key")
        content = patch.get("content", "")
        lane = patch.get("lane", settings.DEFAULT_LANE)

        existing = None
        if key:
            existing = s.exec(select(MemoryItem)
                              .where(MemoryItem.key == key,
                                     MemoryItem.status == "active")).first()

        emb = embed([content])[0] if content else []

        if prop.proposal_type == "add" or not existing:
            source_ids = list(set(prop.evidence_ids))
            tier = (MemoryTier.LONG_TERM
                    if len(source_ids) >= settings.PROMOTE_SEMANTIC_MIN_SOURCES
                    else MemoryTier.WORKING)
            mem = MemoryItem(
                id=new_id("mem"),
                memory_type=mem_type, key=key or content[:60], content=content,
                embedding=emb, tier=tier, source_ids=source_ids,
                confidence=prop.confidence, importance=0.5,
                lane=lane,
            )
            s.add(mem); s.flush()
            _make_checkpoint(s, mem, {}, prop.id, trigger="gate")
            index_memory_item(mem.id, emb, {"key": mem.key, "memory_type": mem.memory_type})
        else:
            before = existing.model_dump(mode="json")
            existing.content = content
            existing.embedding = emb
            existing.version += 1
            existing.updated_at = utcnow()
            existing.source_ids = list(set(existing.source_ids + prop.evidence_ids))
            existing.confidence = max(existing.confidence, prop.confidence)
            s.add(existing); s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="evolve")
            index_memory_item(existing.id, emb, {"key": existing.key, "memory_type": existing.memory_type})

        prop.status = ProposalStatus.APPLIED
        prop.applied_at = utcnow()
        s.add(prop); s.commit()
        return {"ok": True, "proposal_id": prop.id}


def rollback(memory_id: str) -> dict:
    with db.get_session() as s:
        ckpts = s.exec(select(MemoryCheckpoint)
                       .where(MemoryCheckpoint.memory_id == memory_id)
                       .order_by(MemoryCheckpoint.version.desc())).all()
        if len(ckpts) < 2:
            return {"ok": False, "reason": "no previous version"}
        prev = ckpts[1]
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False, "reason": "memory missing"}
        before = mem.model_dump(mode="json")
        for k, v in prev.after.items():
            if hasattr(mem, k) and k != "id":
                setattr(mem, k, v)
        mem.version += 1
        mem.updated_at = utcnow()
        s.add(mem)
        _make_checkpoint(s, mem, before, proposal_id="", trigger="rollback")
        s.commit()
        return {"ok": True}


def delete_memory(memory_id: str) -> dict:
    """删除记忆（从 SQLite + 向量存储）"""
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if mem:
            s.delete(mem)
            s.commit()
    delete_memory_item(memory_id)
    return {"ok": True}
