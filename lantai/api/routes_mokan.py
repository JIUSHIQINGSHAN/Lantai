"""磨勘 REST 路由模块（ADR-0083）。

提供：
1. POST /mokan/verify: 对输入记忆条目执行多源交叉勘验与可信度自证；
2. GET /mokan/audit: 存量事实库矛盾冲突与一致性审计。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user

router = APIRouter(prefix="/mokan", tags=["mokan"])


class MokanVerifyRequest(BaseModel):
    memory_ids: list[str] = Field(default_factory=list, description="待交叉勘验的目标记忆 ID 列表")


@router.post("/verify")
def mokan_verify_endpoint(
    req: MokanVerifyRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """磨勘：多源认知证据交叉勘验与可信度自证（ADR-0083）。"""
    from lantai.services.mokan_service import mokan_verify_service

    try:
        return mokan_verify_service(
            memory_ids=req.memory_ids,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except Exception as e:
        raise HTTPException(422, str(e)) from e


@router.get("/audit")
def mokan_audit_endpoint(
    query: str = Query(default="", description="检索过滤关键字（可选）"),  # noqa: B008
    limit: int = Query(default=50, description="最大审计条数"),  # noqa: B008
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """磨勘：存量事实矛盾冲突与证据链审计（ADR-0083）。"""
    from lantai.services.mokan_service import mokan_audit_service

    try:
        return mokan_audit_service(
            query=query,
            limit=limit,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except Exception as e:
        raise HTTPException(422, str(e)) from e
