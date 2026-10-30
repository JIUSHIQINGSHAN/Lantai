"""知命（ADR-0081 · 不知命无以为君子）：认知生命全周期演化状态机与动态时效衰变服务层。

基于认知层 ZhimingEngine 统一封装：
1. zhiming_inspect_service: 单条记忆生命周期度量探查；
2. zhiming_transition_service: 显式状态机跃迁请求与落库执行；
3. zhiming_audit_service: 全库或租户生命周期巡检与状态机批量推进。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from lantai.cognition.zhiming import (
    LifecyclePhase,
    ZhimingEngine,
    ZhimingItem,
    ZhimingResult,
    ZhimingTransition,
)


def zhiming_inspect_service(
    memory_id: str,
    session: Session | None = None,
    principal=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """探查单条记忆当前生命周期度量与半衰期衰变情况。"""
    engine = ZhimingEngine(session=session, principal=principal)
    item: ZhimingItem = engine.inspect_lifecycle(memory_id=memory_id, now=now)
    return item.to_dict(fenced=True)


def zhiming_transition_service(
    memory_id: str,
    target_phase: str,
    reason: str = "",
    superseded_by: str | None = None,
    apply: bool = False,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """执行或预演单条记忆生命周期状态机跃迁。"""
    engine = ZhimingEngine(session=session, principal=principal)
    phase = LifecyclePhase(target_phase.lower())
    trans: ZhimingTransition = engine.transition_memory(
        memory_id=memory_id,
        target_phase=phase,
        reason=reason,
        superseded_by=superseded_by,
        apply=apply,
    )
    return trans.to_dict()


def zhiming_audit_service(
    auto_apply: bool = False,
    limit: int = 100,
    session: Session | None = None,
    principal=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """批量巡检知识生命周期状态机，推导转移建议并按需落库执行。"""
    engine = ZhimingEngine(session=session, principal=principal)
    res: ZhimingResult = engine.run_lifecycle_audit(
        auto_apply=auto_apply,
        limit=limit,
        now=now,
    )
    return res.to_dict(fenced=True)
