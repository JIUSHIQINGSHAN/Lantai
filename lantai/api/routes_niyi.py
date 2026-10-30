"""拟议（Niyi）REST 路由端点 (ADR-0071)。

对外暴露认知演化提案审议与未决提案冲突审计接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.niyi_service import (
    audit_proposals_service,
    deliberate_proposal_service,
)

router = APIRouter(tags=["niyi"])


class DeliberateRequest(BaseModel):
    proposal_type: str = Field(..., description="提案类型：add | update | merge | deprecate")
    patch_data: dict[str, Any] = Field(default_factory=dict, description="提议的补丁内容")
    target_memory_id: str | None = Field(None, description="目标记忆 ID（若适用）")


@router.post("/niyi/deliberate")
def niyi_deliberate_route(
    req: DeliberateRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """模拟推演提案实施对系统拓扑与认知状态的影响。"""
    return deliberate_proposal_service(
        proposal_type=req.proposal_type,
        patch_data=req.patch_data,
        target_memory_id=req.target_memory_id,
        principal=ctx,
    )


@router.get("/niyi/audit")
def niyi_audit_route(
    limit: int = Query(50, ge=1, le=200, description="最大审计未决提案数"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """审计当前租户范围内的未决提案池，识别互斥与竞争冲突对。"""
    return audit_proposals_service(
        limit=limit,
        principal=ctx,
    )
