from fastapi import APIRouter, HTTPException
from sqlmodel import select

from remembrance.models.tables import MemoryProposal
from remembrance.models.schemas import ProposalDecisionReq, FeedbackReq
from remembrance.models.enums import ProposalStatus
from remembrance.storage.db import get_session
from remembrance.evolution.promoter import apply_proposal, rollback
from remembrance.evolution.reflector import record_feedback
from remembrance.workers.evolve_worker import run_evolve_once

router = APIRouter()


@router.get("/proposals")
def list_proposals(status: str = "pending", limit: int = 50):
    with get_session() as s:
        rows = s.exec(select(MemoryProposal)
                      .where(MemoryProposal.status == status)
                      .order_by(MemoryProposal.created_at.desc())
                      .limit(limit)).all()
        return {"proposals": [r.model_dump(mode="json") for r in rows]}


@router.post("/proposals/{proposal_id}/decide")
def decide_proposal(proposal_id: str, req: ProposalDecisionReq):
    with get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        if not prop:
            raise HTTPException(404, "proposal not found")
        if req.approve:
            prop.status = ProposalStatus.APPROVED
            prop.decided_by = "user"
            s.add(prop); s.commit()
            return apply_proposal(proposal_id)
        else:
            prop.status = ProposalStatus.REJECTED
            prop.decided_by = "user"
            s.add(prop); s.commit()
            return {"ok": True}


@router.post("/memory/{memory_id}/rollback")
def do_rollback(memory_id: str):
    return rollback(memory_id)


@router.post("/feedback")
def feedback(req: FeedbackReq):
    return record_feedback(req.memory_id, req.query, req.helped,
                           req.user_accepted, req.hallucination_risk)


@router.post("/evolve/run")
def evolve_run():
    run_evolve_once()
    return {"ok": True}
