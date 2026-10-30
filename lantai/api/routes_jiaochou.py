"""校雠（Jiaochou）REST 路由端点 (ADR-0070)。

对外暴露双源语义对齐校勘与存量记忆冗余扫描接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.jiaochou_service import (
    collate_memories_service,
    scan_redundancy_service,
)

router = APIRouter(tags=["jiaochou"])


class CollateRequest(BaseModel):
    text_a: str = Field(..., description="待校勘的第一段事实文本")
    text_b: str = Field(..., description="待校勘的第二段事实文本")
    memory_id_a: str | None = Field(None, description="第一段事实所属记忆 ID（可选）")
    memory_id_b: str | None = Field(None, description="第二段事实所属记忆 ID（可选）")


@router.post("/jiaochou/collate")
def jiaochou_collate_route(
    req: CollateRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """对两段事实执行语义校勘对齐、差异分析与融合订误建议。"""
    return collate_memories_service(
        text_a=req.text_a,
        text_b=req.text_b,
        memory_id_a=req.memory_id_a,
        memory_id_b=req.memory_id_b,
        principal=ctx,
    )


@router.get("/jiaochou/scan")
def jiaochou_scan_route(
    limit: int = Query(50, ge=1, le=200, description="最大扫描冗余对数"),
    similarity_threshold: float = Query(0.7, ge=0.1, le=1.0, description="相似度聚类阈值"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """扫描当前权限范围内的活跃记忆近义冗余簇。"""
    return scan_redundancy_service(
        limit=limit,
        similarity_threshold=similarity_threshold,
        principal=ctx,
    )
