"""参数建议路由——薄路由，业务全在 service 层。"""

from fastapi import APIRouter, Depends, Query

from lantai.core.auth import Principal, get_current_user, require_admin
from lantai.parameters import service
from lantai.parameters.schemas import (
    DecisionRequest,
    DecisionResponse,
    OverrideListResponse,
    RollbackRequest,
    RollbackResponse,
    RuntimeParamsResponse,
    SuggestionDetailResponse,
    SuggestionListResponse,
)

router = APIRouter()


@router.get("/param-suggestions", response_model=SuggestionListResponse)
def list_suggestions(
    status: str | None = Query(None, pattern="^(pending|accepted|rejected)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _ctx: Principal = Depends(get_current_user),
):
    return service.list_suggestions(status, limit, offset)


@router.get("/param-suggestions/{suggestion_id}", response_model=SuggestionDetailResponse)
def get_suggestion(suggestion_id: str, _ctx: Principal = Depends(get_current_user)):
    return service.get_suggestion(suggestion_id)


@router.post("/param-suggestions/{suggestion_id}/decision", response_model=DecisionResponse)
def decide_suggestion(
    suggestion_id: str, req: DecisionRequest, ctx: Principal = Depends(require_admin)
):
    """裁决参数建议（票 19 子项 5 拍板：收 admin）。

    accept 是**全局运行参数**变更（ParamOverride revision 链 + settings 原位
    热换，DB 为事实源），多 key 部署下任何 per-user key 可改全局检索行为＝
    跨属主配置投毒；`ctx.user_id` 仍作审计归属（actor）。MCP 零对应工具，
    REST 收紧无跨入口漂移。"""
    return service.decide_suggestion(suggestion_id, req, ctx.user_id)


@router.get("/param-overrides", response_model=OverrideListResponse)
def list_overrides(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _ctx: Principal = Depends(get_current_user),
):
    return service.list_overrides(limit, offset)


@router.post("/param-overrides/{override_id}/rollback", response_model=RollbackResponse)
def rollback_override(
    override_id: str, req: RollbackRequest, ctx: Principal = Depends(require_admin)
):
    """回滚全局参数 override（票 19 子项 5 拍板：收 admin，判据同
    decide_suggestion——head override 快照对调是全局变更）。"""
    return service.rollback_override(override_id, req, ctx.user_id)


@router.get("/runtime-params", response_model=RuntimeParamsResponse)
def runtime_params(_ctx: Principal = Depends(get_current_user)):
    return service.get_effective_params()


@router.post("/param-suggestions/{suggestion_id}/regenerate")
def regenerate_suggestion(suggestion_id: str, ctx: Principal = Depends(require_admin)):
    """拒旧建议并把来源论文重新入队（票 19 子项 5 拍板：收 admin）——驱动
    param_advice_worker 重跑属全局作业面（LLM 由 worker 异步调，非本路由
    外呼）；判据同 decide_suggestion。"""
    return service.regenerate_suggestion(suggestion_id, ctx.user_id)
