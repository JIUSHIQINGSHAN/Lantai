"""底本（Diben）REST 路由端点 (ADR-0072)。

对外暴露跨会话定本提炼锚定、最新底本读取与意图漂移检测接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.diben_service import (
    analyze_drift_service,
    get_active_anchor_service,
    synthesize_anchor_service,
)

router = APIRouter(tags=["diben"])


class AnchorRequest(BaseModel):
    session_id: str = Field(..., description="目标会话 ID（≥3 字符）")
    blocks: dict[str, Any] = Field(..., description="五段认知现场键值对")


class DriftRequest(BaseModel):
    query: str = Field(..., description="用户当前输入文本")
    session_id: str | None = Field(None, description="目标会话 ID（可选）")


@router.post("/diben/anchor")
def diben_anchor_route(
    req: AnchorRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """将当前会话认知现场提炼并固化为底本定本。"""
    return synthesize_anchor_service(
        session_id=req.session_id,
        blocks=req.blocks,
        principal=ctx,
    )


@router.post("/diben/drift")
def diben_drift_route(
    req: DriftRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """分析当前用户输入与激活底本之间的意图延续性与漂移程度。"""
    return analyze_drift_service(
        query=req.query,
        session_id=req.session_id,
        principal=ctx,
    )


@router.get("/diben/latest")
def diben_latest_route(
    session_id: str | None = Query(None, description="指定会话 ID（可选）"),
    ctx=Depends(get_current_user),
) -> dict[str, Any] | None:
    """获取当前租户范围内激活的最新会话底本。"""
    return get_active_anchor_service(
        session_id=session_id,
        principal=ctx,
    )
