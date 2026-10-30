"""披沙 REST 路由模块（ADR-0077）。

提供：
1. POST /pisha/refine: 纯文本或单条候选指代消解与提纯；
2. POST /pisha/batch: 批量模糊带候选扫描提纯。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user

router = APIRouter(prefix="/pisha", tags=["pisha"])


class PishaRefineRequest(BaseModel):
    text: str | None = Field(default=None, description="待精炼的原始文本")
    candidate_id: str | None = Field(default=None, description="待精炼的候选记忆 ID")
    context: str = Field(default="", description="上下文背景信息")
    entity_hints: dict[str, str] = Field(default_factory=dict, description="已知实体指代映射")


class PishaBatchRequest(BaseModel):
    min_conf: float = Field(default=0.15, description="模糊带最小置信度")
    max_conf: float = Field(default=0.6, description="模糊带最大置信度")
    limit: int = Field(default=20, description="最大处理候选条目数")


@router.post("/refine")
def pisha_refine_endpoint(req: PishaRefineRequest, ctx=Depends(get_current_user)) -> dict[str, Any]:
    """披沙：单段文本或单条候选记录指代消解与结构化提纯（ADR-0077）。"""
    from lantai.services.refine_service import pisha_refine_service

    try:
        return pisha_refine_service(
            text=req.text,
            candidate_id=req.candidate_id,
            context=req.context,
            entity_hints=req.entity_hints,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except ValueError as ve:
        status = 404 if "not found" in str(ve) or "未找到" in str(ve) else 422
        raise HTTPException(status, str(ve)) from ve


@router.post("/batch")
def pisha_batch_endpoint(req: PishaBatchRequest, ctx=Depends(get_current_user)) -> dict[str, Any]:
    """披沙：批量扫描模糊带候选执行提纯去噪（ADR-0077）。"""
    from lantai.services.refine_service import batch_refine_candidates

    return batch_refine_candidates(
        min_conf=req.min_conf,
        max_conf=req.max_conf,
        limit=req.limit,
        principal=ctx,
    )
