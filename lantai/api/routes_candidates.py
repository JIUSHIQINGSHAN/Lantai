"""候选可见队列路由（Ticket 02）——薄路由，业务全在 candidate_service。

GET  /candidates/pending       待审候选列表
POST /candidates/{id}/review   审核（approve→提案链 / reject→归档）
"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import CandidateReviewReq
from lantai.services.candidate_service import (
    list_pending_candidates,
    review_candidate,
)

router = APIRouter(tags=["candidates"])


@router.get("/candidates/pending")
def candidates_pending(limit: int = 50):
    return list_pending_candidates(limit)


@router.post("/candidates/{candidate_id}/review")
def candidates_review(candidate_id: str, req: CandidateReviewReq):
    try:
        return review_candidate(candidate_id, approve=req.approve, reason=req.reason)
    except ValueError as e:
        raise HTTPException(404, str(e))
