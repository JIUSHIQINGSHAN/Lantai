"""佐证（Zuozheng）REST 路由端点 (ADR-0068)。

对外暴露记忆证据链深度反查与断言反向佐证印证接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.zuozheng_service import (
    inspect_evidence_service,
    verify_claim_service,
)

router = APIRouter(tags=["zuozheng"])


class VerifyClaimRequest(BaseModel):
    claim: str = Field(..., description="待印证反查的自然语言断言")
    limit: int = Field(5, ge=1, le=20, description="最大召回支撑证据数量")


@router.get("/zuozheng/inspect/{memory_id}")
def zuozheng_inspect_route(
    memory_id: str,
    max_depth: int = Query(2, ge=1, le=5, description="最大证据链反查拓扑深度"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """针对指定记忆 ID 执行多维证据拓扑链深度反查。"""
    res = inspect_evidence_service(memory_id=memory_id, max_depth=max_depth, principal=ctx)
    if not res.get("target_memory_id"):
        raise HTTPException(status_code=404, detail="Memory item not found or access denied")
    return res


@router.post("/zuozheng/verify")
def zuozheng_verify_route(
    req: VerifyClaimRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """针对输入的自然语言断言反查召回支撑证据簇并计算置信度。"""
    return verify_claim_service(claim=req.claim, limit=req.limit, principal=ctx)
