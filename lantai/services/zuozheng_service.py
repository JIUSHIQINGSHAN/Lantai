"""佐证（Zuozheng）服务层——证据链反查与可解释性溯源服务 (ADR-0068)。

为 REST 路由、MCP 工具与认知上下文切面提供证据链深度反查与断言印证服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.zuozheng import ZuozhengEngine
from lantai.core.auth import Principal
from lantai.storage import db


def inspect_evidence_service(
    *,
    memory_id: str,
    max_depth: int = 2,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """针对指定记忆 ID 执行多维证据拓扑链深度反查。"""
    with db.get_session() as session:
        engine = ZuozhengEngine(session=session, principal=principal)
        return engine.inspect_evidence(memory_id=memory_id, max_depth=max_depth)


def verify_claim_service(
    *,
    claim: str,
    limit: int = 5,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """针对输入的自然语言断言反查召回支撑证据簇并计算置信度。"""
    with db.get_session() as session:
        engine = ZuozhengEngine(session=session, principal=principal)
        return engine.verify_claim(claim=claim, limit=limit)
