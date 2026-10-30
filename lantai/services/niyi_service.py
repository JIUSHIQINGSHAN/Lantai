"""拟议（Niyi）服务层——演化提案审议与因果变化推演服务 (ADR-0071)。

为 REST 路由、MCP 工具与认知上下文切面提供变更推演与未决提案审计服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.niyi import NiyiEngine
from lantai.core.auth import Principal
from lantai.storage import db


def deliberate_proposal_service(
    *,
    proposal_type: str,
    patch_data: dict[str, Any],
    target_memory_id: str | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """模拟推演提案实施对系统拓扑与认知状态的影响。"""
    with db.get_session() as session:
        engine = NiyiEngine(session=session, principal=principal)
        return engine.deliberate_proposal(
            proposal_type=proposal_type,
            patch_data=patch_data,
            target_memory_id=target_memory_id,
        )


def audit_proposals_service(
    *,
    limit: int = 50,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """审计当前租户范围内的未决提案池，识别互斥与竞争冲突对。"""
    with db.get_session() as session:
        engine = NiyiEngine(session=session, principal=principal)
        return engine.audit_pending_proposals(limit=limit)
