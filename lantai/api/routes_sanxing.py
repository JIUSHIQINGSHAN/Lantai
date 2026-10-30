"""三省 REST 路由模块（ADR-0079）。

提供：
1. GET /sanxing/diagnose: 扫描并获取当前存量记忆健康审计报告；
2. POST /sanxing/remedy: 执行自愈策略推导与一键状态机落库修复。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user

router = APIRouter(prefix="/sanxing", tags=["sanxing"])


class SanxingRemedyRequest(BaseModel):
    auto_apply: bool = Field(
        default=False, description="是否就地执行自愈落库修复（默认 False 仅预演）"
    )


@router.get("/diagnose")
def sanxing_diagnose_endpoint(ctx=Depends(get_current_user)) -> dict[str, Any]:
    """三省：存量记忆健康审计与多维缺陷扫描（ADR-0079）。"""
    from lantai.services.sanxing_service import sanxing_diagnose_service

    return sanxing_diagnose_service(principal=ctx)


@router.post("/remedy")
def sanxing_remedy_endpoint(
    req: SanxingRemedyRequest, ctx=Depends(get_current_user)
) -> dict[str, Any]:
    """三省：认知自省自愈推导与一键处置修复（ADR-0079）。"""
    from lantai.services.sanxing_service import sanxing_remedy_service

    return sanxing_remedy_service(auto_apply=req.auto_apply, principal=ctx)
