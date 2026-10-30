"""三省（ADR-0079 · 吾日三省吾身）：反思自省与记忆健康自愈服务层。

基于认知层 SanxingEngine 统一封装：
1. sanxing_diagnose_service: 存量记忆库健康审计与多维缺陷扫描；
2. sanxing_remedy_service: 结构化自愈策略生成与一键状态机落库执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from lantai.cognition.sanxing import SanxingEngine, SanxingRemedyResult
from lantai.storage import db


def sanxing_diagnose_service(
    session: Session | None = None,
    principal=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """执行三省健康自检并返回结构化审计报告字典。"""
    engine = SanxingEngine(session=session, principal=principal)
    report = engine.generate_audit_report(now=now)
    return report.model_dump(mode="json")


def sanxing_remedy_service(
    auto_apply: bool = False,
    session: Session | None = None,
    principal=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """执行三省反思自愈，推导修复处置方案，按需一键落库（宁 miss 不脏写）。"""
    engine = SanxingEngine(session=session, principal=principal)
    res: SanxingRemedyResult = engine.generate_remedies(auto_apply=auto_apply, now=now)
    return res.to_dict(fenced=True)
