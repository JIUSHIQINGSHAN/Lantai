"""底本（Diben）服务层——跨会话现场定本与认知状态传承服务 (ADR-0072)。

为 REST 路由、MCP 工具与认知上下文切面提供定本锚定与意图漂移分析服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.diben import DibenEngine
from lantai.core.auth import Principal
from lantai.storage import db


def synthesize_anchor_service(
    *,
    session_id: str,
    blocks: dict[str, Any],
    principal: Principal | None = None,
) -> dict[str, Any]:
    """将五段认知现场提炼并固化为当前会话底本。"""
    with db.get_session() as session:
        engine = DibenEngine(session=session, principal=principal)
        return engine.synthesize_anchor(session_id=session_id, blocks=blocks)


def analyze_drift_service(
    *,
    query: str,
    session_id: str | None = None,
    anchor: dict[str, Any] | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """分析当前用户输入与激活底本之间的意图延续性与漂移程度。"""
    with db.get_session() as session:
        engine = DibenEngine(session=session, principal=principal)
        return engine.analyze_drift(current_query=query, anchor=anchor, session_id=session_id)


def get_active_anchor_service(
    *,
    session_id: str | None = None,
    principal: Principal | None = None,
) -> dict[str, Any] | None:
    """检索当前租户范围内激活的最新会话底本。"""
    with db.get_session() as session:
        engine = DibenEngine(session=session, principal=principal)
        return engine.get_active_anchor(session_id=session_id)
