"""校雠（Jiaochou）服务层——智能语义去重与知识对齐订误服务 (ADR-0070)。

为 REST 路由、MCP 工具与认知上下文切面提供事实校勘对齐与存量冗余扫描服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.jiaochou import JiaochouEngine
from lantai.core.auth import Principal
from lantai.storage import db


def collate_memories_service(
    *,
    text_a: str,
    text_b: str,
    memory_id_a: str | None = None,
    memory_id_b: str | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """对两段文本或记忆执行语义校勘对齐与订误分析。"""
    with db.get_session() as session:
        engine = JiaochouEngine(session=session, principal=principal)
        return engine.compare_and_collate(
            text_a=text_a,
            text_b=text_b,
            memory_id_a=memory_id_a,
            memory_id_b=memory_id_b,
        )


def scan_redundancy_service(
    *,
    limit: int = 50,
    similarity_threshold: float = 0.7,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """扫描当前权限范围内的活跃记忆，查找近义冗余记忆簇。"""
    with db.get_session() as session:
        engine = JiaochouEngine(session=session, principal=principal)
        return engine.scan_redundancy(
            limit=limit,
            similarity_threshold=similarity_threshold,
        )
