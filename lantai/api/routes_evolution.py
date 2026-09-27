from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.models.schemas import FeedbackReq, ProposalDecisionReq
from lantai.services.evolution_service import (
    decide_proposal,
    do_rollback,
    list_proposals,
    record_feedback_entry,
    run_evolve,
)

router = APIRouter()


def _ok_or_raise(result: dict) -> dict:
    """service 的 `{"ok": False, ...}` → 对应 4xx（票 .scratch/api-error-status/01）。

    service 层失败多以返回 dict 表达（被 worker / eval / MCP 多处消费，形状不能动），
    但 HTTP 客户端看到的是 200 + ok:false——语义上成功、实际失败，调用方必须翻 body
    才知道出错了。此处只在**路由边界**翻译状态码，service 契约零变更。
    """
    if isinstance(result, dict) and result.get("ok") is False:
        reason = str(result.get("reason") or "operation failed")
        if "not found" in reason or "missing" in reason:
            raise HTTPException(404, reason)
        if (
            "not applicable" in reason
            or "state changed" in reason
            or "already" in reason
            or "ambiguous" in reason
        ):
            # 资源在、但当前状态不允许该操作（stale 硬门 / 多义寻址 / 已 apply）
            raise HTTPException(409, reason)
        raise HTTPException(422, reason)
    return result


@router.get("/proposals")
def list_proposals_route(status: str = "pending", limit: int = 50, ctx=Depends(get_current_user)):
    """提案列表。

    归属（票 .scratch/readside-gaps/02）：此前不带身份全表捞，`reason`
    常含记忆正文，任何登录用户都能读到别人的待决内容。
    """
    return list_proposals(status, limit, principal=ctx)


@router.post("/proposals/{proposal_id}/decide")
def decide_proposal_route(
    proposal_id: str, req: ProposalDecisionReq, ctx=Depends(get_current_user)
):
    """裁决提案。归属校验在 service 层（票 readside-gaps/02）：approve 会
    直接 `apply_proposal` 写库，A 点批准就等于能往库里塞内容。"""
    try:
        return _ok_or_raise(decide_proposal(proposal_id, req, principal=ctx))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        status = 404 if "not found" in str(e) else 422
        raise HTTPException(status, str(e)) from e


@router.post("/memory/{memory_id}/rollback")
def do_rollback_route(memory_id: str):
    return _ok_or_raise(do_rollback(memory_id))


@router.post("/feedback")
def feedback_route(req: FeedbackReq):
    return _ok_or_raise(record_feedback_entry(req))


@router.post("/evolve/run")
def evolve_run_route():
    return run_evolve()


@router.post("/evolution/kaogong")
def kaogong_run_route():
    """考功（ADR-0031）：执行一次全库记忆价值演化考评周期。"""
    from lantai.services.kaogong_service import run_kaogong_cycle

    return run_kaogong_cycle()


@router.get("/evolution/kaogong/report")
def kaogong_report_route():
    """考功（ADR-0031）：获取最新考功评定审计报告。"""
    from lantai.services.kaogong_service import get_kaogong_report

    return get_kaogong_report()


@router.post("/evolution/consolidate")
def consolidate_run_route():
    """沉潜（ADR-0036）：执行一次闲时夜梦记忆沉淀与折叠压缩周期。"""
    from lantai.services.consolidation_service import run_consolidation_cycle

    return run_consolidation_cycle()


@router.get("/evolution/consolidate/report")
def consolidate_report_route():
    """沉潜（ADR-0036）：获取最新夜梦沉淀审计报告。"""
    from lantai.services.consolidation_service import get_consolidation_report

    return get_consolidation_report()


class EpisodeFeedbackReq(BaseModel):
    """轨迹级奖励回传（v022 票据 06，最小切片：只登记不接权重）。"""

    session_id: str = Field(min_length=1, max_length=128)
    outcome: str = "neutral"  # success / failure / neutral
    steps: list[dict]  # [{"memory_id": str, "rank": int|None}, ...] 按时间序
    lam: float = 0.5
    gamma: float = 0.9
    user_id: str | None = None


@router.post("/evolve/episode/feedback")
def episode_feedback_route(req: EpisodeFeedbackReq):
    """轨迹信用登记（上游 aiduMEI v21.2 M1 同名端点语义）：按位置回传并聚合。

    只写 episode 侧表，不碰记忆正文；credit 不进入检索打分（察窗纪律）。"""
    from lantai.services.episode_service import record_episode

    try:
        return record_episode(
            session_id=req.session_id,
            outcome=req.outcome,
            steps=req.steps,
            lam=req.lam,
            gamma=req.gamma,
            user_id=req.user_id,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/evolve/episode/credits")
def episode_credits_route(memory_ids: str | None = None):
    """聚合各记忆累计信用（只读视图；检索打分不读它）。"""
    from lantai.services.episode_service import episode_credit_map

    ids = [m.strip() for m in memory_ids.split(",") if m.strip()] if memory_ids else None
    return {"credits": episode_credit_map(ids)}
