"""起居注（Qijuzhu）REST 路由端点 (ADR-0066)。

对外暴露今日起居注全息纪事、滑动时间轴脉络与指定历史日期回放。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from lantai.core.auth import get_current_user
from lantai.services.qijuzhu_service import (
    get_daily_chronicle_service,
    get_timeline_service,
)

router = APIRouter(tags=["qijuzhu"])


@router.get("/qijuzhu/today")
def qijuzhu_today_route(
    detail_level: Literal["summary", "full"] = Query(
        "summary", description="纪事详尽程度：summary 或 full"
    ),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取今日起居注全息纪事。"""
    return get_daily_chronicle_service(
        date_str=None,
        detail_level=detail_level,
        principal=ctx,
    )


@router.get("/qijuzhu/timeline")
def qijuzhu_timeline_route(
    days: int = Query(3, ge=1, le=30, description="回溯滑动天数 [1, 30]"),
    limit: int = Query(50, ge=1, le=200, description="最大返回事件条数 [1, 200]"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取滑动时间轴事件脉络流。"""
    return get_timeline_service(
        days=days,
        limit=limit,
        principal=ctx,
    )


@router.get("/qijuzhu/dates/{date_str}")
def qijuzhu_date_route(
    date_str: str,
    detail_level: Literal["summary", "full"] = Query(
        "summary", description="纪事详尽程度：summary 或 full"
    ),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取指定历史日期的起居注全息纪事 (YYYY-MM-DD)。"""
    return get_daily_chronicle_service(
        date_str=date_str,
        detail_level=detail_level,
        principal=ctx,
    )
