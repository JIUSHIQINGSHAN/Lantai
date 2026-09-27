"""候选可见队列路由（Ticket 02）——薄路由，业务全在 candidate_service。

归属（票 .scratch/readside-gaps/02）：这些 handler 此前一个身份都不取，
后果两条——读侧 `/candidates/pending` 全表捞（而这个端点正是 AGENTS.md
「宁 miss 不脏写」的裁决入口）；写侧 A 能对 B 的候选 review/defer/refine，
**替别人做校验判断**。校验在 service 层（worker/MCP 也走那条路），
这里只负责把 principal 传下去。

GET  /candidates/pending       待审候选列表
POST /candidates/{id}/review   审核（approve→提案链 / reject→归档）
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
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
def candidates_pending(limit: int = 50, ctx=Depends(get_current_user)):
    return list_pending_candidates(limit, principal=ctx)


@router.post("/candidates/{candidate_id}/review")
def candidates_review(candidate_id: str, req: CandidateReviewReq, ctx=Depends(get_current_user)):
    try:
        return review_candidate(candidate_id, approve=req.approve, reason=req.reason, principal=ctx)
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/{candidate_id}/defer")
def candidates_defer(candidate_id: str, req: CandidateDeferReq, ctx=Depends(get_current_user)):
    try:
        return defer_candidate(
            candidate_id, req.days, req.reason, req.expected_review_due_at, principal=ctx
        )
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/{candidate_id}/defer/undo")
def candidates_defer_undo(
    candidate_id: str, req: CandidateDeferUndoReq, ctx=Depends(get_current_user)
):
    try:
        return undo_candidate_defer(candidate_id, req.expected_review_due_at, principal=ctx)
    except CandidateStateConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/{candidate_id}/refine")
def candidates_refine(candidate_id: str, ctx=Depends(get_current_user)):
    """披沙（ADR-0030）：对单条候选记忆进行指代消解与提纯。

    归属校验（票 readside-gaps/02）：refine 会改写 B 的 summary、claims、
    lane，甚至把 status 改成 rejected——同 review 口径。
    """
    try:
        from lantai.services.refine_service import refine_candidate_record

        return refine_candidate_record(candidate_id, principal=ctx)
    except ValueError as e:
        status = 404 if "not found" in str(e) or "未找到" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/candidates/batch_refine")
def candidates_batch_refine(
    min_conf: float = 0.15, max_conf: float = 0.6, limit: int = 20, ctx=Depends(get_current_user)
):
    """披沙（ADR-0030）：批量对模糊区间的候选执行提纯。

    归属（票 readside-gaps/02）：`limit` 是全表捞的——A 一次批量会把 B
    的待审候选捞进来改写甚至驳回。按 viewer 收窄查询。
    """
    from lantai.services.refine_service import batch_refine_candidates

    return batch_refine_candidates(min_conf=min_conf, max_conf=max_conf, limit=limit, principal=ctx)


@router.post("/candidates/ai_triage")
def candidates_ai_triage(limit: int = 50, ctx=Depends(get_current_user)):
    """AI 智能预审：扫描待审候选并返回智能研判与决策建议。

    归属（票 readside-gaps/02）：把候选 `text` 送进 LLM，全表捞就是
    把别人的待审正文送出去。按 viewer 收窄。
    """
    from lantai.services.auto_triage_service import run_ai_triage

    return run_ai_triage(limit=limit, principal=ctx)


@router.post("/candidates/batch_apply_triage")
def candidates_batch_apply_triage(req: dict, ctx=Depends(get_current_user)):
    """批量采纳 AI 预审决策（一键批量批准/淘汰/提纯）。

    归属（票 readside-gaps/02）：请求体里的 `id` 是调用方给的，与预审
    返回的建议列表**没有绑定关系**——A 直接 POST `{"id": "cand-B",
    "action": "reject"}` 即可驳回 B 的候选。校验在 service 层逐条执行。
    """
    from lantai.services.auto_triage_service import apply_ai_triage_batch

    actions = req.get("actions", [])
    if not isinstance(actions, list):
        raise HTTPException(422, "actions 必须为列表")
    return apply_ai_triage_batch(actions, principal=ctx)
