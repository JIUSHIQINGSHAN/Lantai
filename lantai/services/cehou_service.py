"""测候（Cehou）服务层——影子观察期巡检与演化状态监控服务 (ADR-0073)。

为 REST 路由、MCP 工具与认知上下文切面提供视窗巡检、视窗扫描与样本上报服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.cehou import CehouEngine
from lantai.core.auth import Principal
from lantai.storage import db


def inspect_cehou_window_service(
    *,
    window_id: str,
    mock_days_observed: int | None = None,
    principal: Principal | None = None,
) -> dict[str, Any] | None:
    """巡检指定影子观察视窗，评估累积实测指标并裁决状态跃迁。"""
    with db.get_session() as session:
        engine = CehouEngine(session=session, principal=principal)
        return engine.inspect_window(window_id=window_id, mock_days_observed=mock_days_observed)


def scan_cehou_windows_service(
    *,
    target_type: str | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """全域扫描当前租户内活跃影子视窗并生成巡检汇总报告。"""
    with db.get_session() as session:
        engine = CehouEngine(session=session, principal=principal)
        return engine.scan_active_windows(target_type=target_type)


def record_cehou_sample_service(
    *,
    window_id: str,
    success: bool,
    latency_ms: float = 0.0,
    score_delta: float = 0.0,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """向指定影子视窗上报一条实测运行样本。"""
    with db.get_session() as session:
        engine = CehouEngine(session=session, principal=principal)
        return engine.record_sample(
            window_id=window_id,
            success=success,
            latency_ms=latency_ms,
            score_delta=score_delta,
        )


def create_cehou_window_service(
    *,
    window_id: str,
    target_type: str,
    target_id: str,
    required_days: int = 7,
    min_samples: int = 10,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """创建或注册一个影子观察期视窗。"""
    with db.get_session() as session:
        engine = CehouEngine(session=session, principal=principal)
        return engine.create_observation_window(
            window_id=window_id,
            target_type=target_type,
            target_id=target_id,
            required_days=required_days,
            min_samples=min_samples,
        )
