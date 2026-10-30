"""测候（Cehou）REST 路由端点 (ADR-0073)。

对外暴露影子观察视窗巡检、视窗扫描与样本实测上报接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.cehou_service import (
    create_cehou_window_service,
    inspect_cehou_window_service,
    record_cehou_sample_service,
    scan_cehou_windows_service,
)

router = APIRouter(tags=["cehou"])


class CreateWindowRequest(BaseModel):
    window_id: str = Field(..., description="观察视窗 ID")
    target_type: str = Field(..., description="目标类型：param | rule | proposal")
    target_id: str = Field(..., description="目标实体 ID")
    required_days: int = Field(7, ge=1, le=90, description="必须达标观察天数")
    min_samples: int = Field(10, ge=1, le=1000, description="必须达标样本数")


class InspectRequest(BaseModel):
    window_id: str = Field(..., description="目标视窗 ID")
    mock_days_observed: int | None = Field(None, description="模拟观察天数（测试/巡检指定）")


class SampleRequest(BaseModel):
    window_id: str = Field(..., description="目标视窗 ID")
    success: bool = Field(..., description="本次运行是否成功")
    latency_ms: float = Field(0.0, description="运行耗时 (ms)")
    score_delta: float = Field(0.0, description="评分增量")


@router.post("/cehou/windows")
def cehou_create_window_route(
    req: CreateWindowRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """创建或注册一个影子观察期视窗。"""
    return create_cehou_window_service(
        window_id=req.window_id,
        target_type=req.target_type,
        target_id=req.target_id,
        required_days=req.required_days,
        min_samples=req.min_samples,
        principal=ctx,
    )


@router.post("/cehou/inspect")
def cehou_inspect_route(
    req: InspectRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any] | None:
    """巡检指定影子观察视窗，评估累积实测指标并裁决状态跃迁。"""
    return inspect_cehou_window_service(
        window_id=req.window_id,
        mock_days_observed=req.mock_days_observed,
        principal=ctx,
    )


@router.get("/cehou/windows")
def cehou_scan_windows_route(
    target_type: str | None = Query(None, description="限定目标类型：param | rule | proposal"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """全域扫描当前租户内活跃影子视窗并生成巡检汇总报告。"""
    return scan_cehou_windows_service(
        target_type=target_type,
        principal=ctx,
    )


@router.post("/cehou/sample")
def cehou_sample_route(
    req: SampleRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """向指定影子视窗上报一条实测运行样本。"""
    return record_cehou_sample_service(
        window_id=req.window_id,
        success=req.success,
        latency_ms=req.latency_ms,
        score_delta=req.score_delta,
        principal=ctx,
    )
