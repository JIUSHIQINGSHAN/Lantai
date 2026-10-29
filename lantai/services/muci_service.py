"""目次（Muci）服务层——多维知识目录聚合服务 (ADR-0065)。

为 REST 路由、MCP 工具与认知上下文切面提供结构化知识目录聚合服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.muci import MuciEngine
from lantai.core.auth import Principal
from lantai.storage import db


def get_catalog_service(
    *,
    principal: Principal | None = None,
    domain: str | None = None,
    lane: str | None = None,
    detail_level: str = "compact",
    max_entries_per_section: int = 3,
) -> dict[str, Any]:
    """获取多维知识目录服务。"""
    with db.get_session() as session:
        engine = MuciEngine(session=session, principal=principal)
        return engine.build_catalog(
            domain=domain,
            lane=lane,
            detail_level=detail_level,
            max_entries_per_section=max_entries_per_section,
        )
