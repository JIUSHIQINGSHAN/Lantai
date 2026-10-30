"""知命 REST 路由模块（ADR-0081）。

提供：
1. GET /lifecycle/inspect: 单条记忆生命周期度量探查；
2. POST /lifecycle/transition: 显式状态机跃迁（支持预演与就地落库）；
3. POST /lifecycle/audit: 批量生命周期巡检与状态机自适应演进。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user

router = APIRouter(prefix="/lifecycle", tags=["zhiming"])


class LifecycleTransitionRequest(BaseModel):
    memory_id: str = Field(description="目标记忆 ID")
    target_phase: str = Field(
        description="目标生命周期阶段 (candidate/active/weakened/superseded/retired)"
    )
    reason: str = Field(default="", description="转移理由")
    superseded_by: str | None = Field(
        default=None, description="当阶段为 superseded 时指向的新记忆 ID"
    )
    apply: bool = Field(default=False, description="是否就地写库落盘 (默认 False 仅预演)")


class LifecycleAuditRequest(BaseModel):
    auto_apply: bool = Field(default=False, description="是否自动将转移建议落库执行")
    limit: int = Field(default=100, description="最大巡检条数上限")


@router.get("/inspect")
def lifecycle_inspect_endpoint(
    memory_id: str = Query(..., description="记忆 ID"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """知命：单条记忆生命周期度量与半衰期衰变探查（ADR-0081）。"""
    from lantai.services.zhiming_service import zhiming_inspect_service

    try:
        return zhiming_inspect_service(memory_id=memory_id, principal=ctx)
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except ValueError as ve:
        raise HTTPException(404, str(ve)) from ve
    except Exception as e:
        raise HTTPException(422, str(e)) from e


@router.post("/transition")
def lifecycle_transition_endpoint(
    req: LifecycleTransitionRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """知命：单条记忆生命周期状态机跃迁请求（ADR-0081）。"""
    from lantai.services.zhiming_service import zhiming_transition_service

    try:
        return zhiming_transition_service(
            memory_id=req.memory_id,
            target_phase=req.target_phase,
            reason=req.reason,
            superseded_by=req.superseded_by,
            apply=req.apply,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except ValueError as ve:
        raise HTTPException(422, str(ve)) from ve
    except Exception as e:
        raise HTTPException(422, str(e)) from e


@router.post("/audit")
def lifecycle_audit_endpoint(
    req: LifecycleAuditRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """知命：知识生命周期巡检与状态机批量推进（ADR-0081）。"""
    from lantai.services.zhiming_service import zhiming_audit_service

    try:
        return zhiming_audit_service(
            auto_apply=req.auto_apply,
            limit=req.limit,
            principal=ctx,
        )
    except Exception as e:
        raise HTTPException(422, str(e)) from e
