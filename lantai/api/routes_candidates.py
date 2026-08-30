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


@router.post("/candidates/{candidate_id}/refine")
def candidates_refine(candidate_id: str):
    """披沙（ADR-0030）：对单条候选记忆进行指代消解与提纯。"""
    try:
        from lantai.services.refine_service import refine_candidate_record
        return refine_candidate_record(candidate_id)
    except ValueError as e:
        status = 404 if "not found" in str(e) or "未找到" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/batch_refine")
def candidates_batch_refine(min_conf: float = 0.15, max_conf: float = 0.6, limit: int = 20):
    """披沙（ADR-0030）：批量对模糊区间的候选执行提纯。"""
    from lantai.services.refine_service import batch_refine_candidates
    return batch_refine_candidates(min_conf=min_conf, max_conf=max_conf, limit=limit)

