"""磨勘（ADR-0083 · 磨勘之法）：多源认知证据交叉勘验与可信度自证服务层。

基于认知层 MokanEngine 统一封装：
1. mokan_verify_service: 对特定记忆条目或事实执行多源交叉勘验；
2. mokan_audit_service: 对存量记忆执行全局或按需的矛盾冲突与一致性审计。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from lantai.cognition.mokan import MokanAuditResult, MokanEngine, MokanVerificationItem


def mokan_verify_service(
    memory_ids: list[str] | None = None,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """对一组记忆条目执行多源交叉勘验与可信度自证。"""
    engine = MokanEngine(session=session, principal=principal)
    items: list[MokanVerificationItem] = engine.cross_verify_memories(
        memory_ids=memory_ids,
        session=session,
    )
    verified_count = sum(1 for it in items if it.verification_status == "verified")
    disputed_count = sum(1 for it in items if it.verification_status == "disputed")
    unverified_count = sum(1 for it in items if it.verification_status == "unverified")

    return {
        "total_verified": len(items),
        "verified_count": verified_count,
        "disputed_count": disputed_count,
        "unverified_count": unverified_count,
        "items": [it.to_dict(fenced=True) for it in items],
    }


def mokan_audit_service(
    query: str = "",
    limit: int = 50,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """审计存量事实的交叉一致性与未决矛盾冲突。"""
    engine = MokanEngine(session=session, principal=principal)
    res: MokanAuditResult = engine.audit_contradictions(
        query=query,
        limit=limit,
        session=session,
    )
    return res.to_dict(fenced=True)
