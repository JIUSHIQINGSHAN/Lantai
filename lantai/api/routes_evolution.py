from fastapi import APIRouter, HTTPException

from lantai.models.schemas import ProposalDecisionReq, FeedbackReq
from lantai.services.evolution_service import (
    list_proposals,
    decide_proposal,
    do_rollback,
    record_feedback_entry,
    run_evolve,
)

router = APIRouter()


@router.get("/proposals")
def list_proposals_route(status: str = "pending", limit: int = 50):
    return list_proposals(status, limit)


@router.post("/proposals/{proposal_id}/decide")
def decide_proposal_route(proposal_id: str, req: ProposalDecisionReq):
    try:
        return decide_proposal(proposal_id, req)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/memory/{memory_id}/rollback")
def do_rollback_route(memory_id: str):
    return do_rollback(memory_id)


@router.post("/feedback")
def feedback_route(req: FeedbackReq):
    return record_feedback_entry(req)


@router.post("/evolve/run")
def evolve_run_route():
    return run_evolve()
