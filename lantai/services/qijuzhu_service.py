"""起居注（Qijuzhu）服务层——时序记忆脉络与每日全息纪事服务 (ADR-0066)。

为 REST 路由、MCP 工具与认知上下文切面提供时序演进事件聚合服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.qijuzhu import QijuzhuEngine
from lantai.core.auth import Principal
from lantai.storage import db


def get_daily_chronicle_service(
    *,
    date_str: str | None = None,
    detail_level: str = "summary",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """获取指定日期的全息起居注纪事。"""
    with db.get_session() as session:
        engine = QijuzhuEngine(session=session, principal=principal)
        return engine.get_daily_chronicle(date=date_str, detail_level=detail_level)


def get_timeline_service(
    *,
    days: int = 3,
    limit: int = 50,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """获取滑动时间窗口内的时序记忆脉络流。"""
    with db.get_session() as session:
        engine = QijuzhuEngine(session=session, principal=principal)
        return engine.get_timeline(days=days, limit=limit)
