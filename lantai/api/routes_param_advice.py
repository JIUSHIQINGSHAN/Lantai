"""参数建议路由——薄路由，业务全在 service 层。"""
from fastapi import APIRouter, Depends, Query

from lantai.core.auth import get_current_user, SecurityContext
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
    _ctx: SecurityContext = Depends(get_current_user),
):
    return service.list_suggestions(status, limit, offset)


@router.get("/param-suggestions/{suggestion_id}",
            response_model=SuggestionDetailResponse)
def get_suggestion(suggestion_id: str,
                   _ctx: SecurityContext = Depends(get_current_user)):
    return service.get_suggestion(suggestion_id)


@router.post("/param-suggestions/{suggestion_id}/decision",
             response_model=DecisionResponse)
def decide_suggestion(suggestion_id: str, req: DecisionRequest,
                      ctx: SecurityContext = Depends(get_current_user)):
    return service.decide_suggestion(suggestion_id, req, ctx.user_id)


@router.get("/param-overrides", response_model=OverrideListResponse)
def list_overrides(limit: int = Query(20, ge=1, le=100),
                   offset: int = Query(0, ge=0),
                   _ctx: SecurityContext = Depends(get_current_user)):
    return service.list_overrides(limit, offset)


@router.post("/param-overrides/{override_id}/rollback",
             response_model=RollbackResponse)
def rollback_override(override_id: str, req: RollbackRequest,
                      ctx: SecurityContext = Depends(get_current_user)):
    return service.rollback_override(override_id, req, ctx.user_id)


@router.get("/runtime-params", response_model=RuntimeParamsResponse)
def runtime_params(_ctx: SecurityContext = Depends(get_current_user)):
    return service.get_effective_params()


@router.post("/param-suggestions/{suggestion_id}/regenerate")
def regenerate_suggestion(suggestion_id: str,
                          ctx: SecurityContext = Depends(get_current_user)):
    return service.regenerate_suggestion(suggestion_id, ctx.user_id)
