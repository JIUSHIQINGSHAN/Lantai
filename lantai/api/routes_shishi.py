"""石室（Shishi）REST 路由端点 (ADR-0074)。

对外暴露高密核心记忆金匮封存、防篡改验真与封存块检索接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.shishi_service import (
    get_sealed_blocks_service,
    seal_core_memory_service,
    verify_shishi_integrity_service,
)

router = APIRouter(tags=["shishi"])


class SealBlockRequest(BaseModel):
    block: str = Field(..., description="核心记忆块名：identity | policy | task")
    content: str = Field(..., min_length=1, description="核心记忆内容正文")
    is_immutable: bool = Field(True, description="是否金匮锁定（锁定后非管理员不可覆写）")
    namespace: str = Field("default", description="命名空间")


@router.post("/shishi/seal")
def shishi_seal_block_route(
    req: SealBlockRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """金匮封存或更新核心记忆块。"""
    return seal_core_memory_service(
        block=req.block,
        content=req.content,
        is_immutable=req.is_immutable,
        namespace=req.namespace,
        principal=ctx,
    )


@router.get("/shishi/verify")
def shishi_verify_integrity_route(
    namespace: str = Query("default", description="命名空间"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """对指定命名空间下的核心记忆进行防篡改完整性验真。"""
    return verify_shishi_integrity_service(
        namespace=namespace,
        principal=ctx,
    )


@router.get("/shishi/blocks")
def shishi_get_sealed_blocks_route(
    namespace: str = Query("default", description="命名空间"),
    ctx=Depends(get_current_user),
) -> list[dict[str, Any]]:
    """读取已封存的核心记忆块（强制樊篱包裹）。"""
    return get_sealed_blocks_service(
        namespace=namespace,
        principal=ctx,
    )
