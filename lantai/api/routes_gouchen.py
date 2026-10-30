"""钩沉 REST 路由模块（ADR-0082）。

提供：
1. POST /gouchen/dredge: 沉埋记忆穿透打捞（关键字 + 沉没度过滤）；
2. GET /gouchen/snapshot: 基于时间锚点的历史语境无污染快照重建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user

router = APIRouter(prefix="/gouchen", tags=["gouchen"])


class GouchenDredgeRequest(BaseModel):
    query: str = Field(default="", description="关键字过滤（空字符串表示全库打捞）")
    min_sunken_score: float = Field(
        default=0.4, description="沉没度最低阈值（0.0~1.0，越高越沉埋）"
    )
    limit: int = Field(default=50, description="最大打捞条数")


@router.post("/dredge")
def gouchen_dredge_endpoint(
    req: GouchenDredgeRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """钩沉：穿透打捞沉埋、归档或弱化的历史记忆（ADR-0082）。"""
    from lantai.services.gouchen_service import gouchen_dredge_service

    try:
        return gouchen_dredge_service(
            query=req.query,
            min_sunken_score=req.min_sunken_score,
            limit=req.limit,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except Exception as e:
        raise HTTPException(422, str(e)) from e


@router.get("/snapshot")
def gouchen_snapshot_endpoint(
    anchor_time: datetime = Query(..., description="历史时间锚点（ISO 8601，UTC）"),  # noqa: B008
    limit: int = Query(default=50, description="最大条数"),  # noqa: B008
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """钩沉：基于时间锚点重建历史语境快照，杜绝未来信息污染（ADR-0082）。"""
    from lantai.services.gouchen_service import gouchen_snapshot_service

    try:
        return gouchen_snapshot_service(
            anchor_time=anchor_time,
            limit=limit,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except Exception as e:
        raise HTTPException(422, str(e)) from e
