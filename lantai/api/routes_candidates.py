"""候选可见队列路由（Ticket 02）——薄路由，业务全在 candidate_service。

GET  /candidates/pending       待审候选列表
POST /candidates/{id}/review   审核（approve→提案链 / reject→归档）
"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import CandidateDeferReq, CandidateDeferUndoReq, CandidateReviewReq
from lantai.services.candidate_service import (
    CandidateStateConflict,
    defer_candidate,
    list_pending_candidates,
    review_candidate,
    undo_candidate_defer,
)

router = APIRouter(tags=["candidates"])


@router.get("/candidates/pending")
def candidates_pending(limit: int = 50):
    return list_pending_candidates(limit)


@router.post("/candidates/{candidate_id}/review")
def candidates_review(candidate_id: str, req: CandidateReviewReq):
    try:
        return review_candidate(candidate_id, approve=req.approve, reason=req.reason)
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/{candidate_id}/defer")
def candidates_defer(candidate_id: str, req: CandidateDeferReq):
    try:
        return defer_candidate(candidate_id, req.days, req.reason,
                               req.expected_review_due_at)
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/{candidate_id}/defer/undo")
def candidates_defer_undo(candidate_id: str, req: CandidateDeferUndoReq):
    try:
        return undo_candidate_defer(candidate_id, req.expected_review_due_at)
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e
