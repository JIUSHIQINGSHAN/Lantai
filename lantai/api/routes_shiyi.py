"""拾遗（Shiyi）REST 路由端点 (ADR-0069)。

对外暴露遗漏记忆自适应补救召回接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.shiyi_service import (
    remedy_pipeline_service,
    remedy_recall_service,
)

router = APIRouter(tags=["shiyi"])


class RemedyRecallRequest(BaseModel):
    query: str = Field(..., description="待执行补救召回的查询文本")
    domain: str = Field("all", description="限定辨域：user/session/agent/all")
    allow_domain_relax: bool = Field(False, description="首轮失配时是否允许放宽辨域")
    limit: int = Field(5, ge=1, le=20, description="最大返回补救条数")


class RemedyPipelineRequest(BaseModel):
    query: str = Field(..., description="主流水线检索与自适应补救查询文本")
    top_k: int = Field(5, ge=1, le=50, description="最大召回条数")
    domain: str | None = Field(None, description="限定辨域：user/session/agent")
    allow_shiyi: bool = Field(True, description="零召回时是否允许自适应唤醒拾遗补偿")


@router.post("/shiyi/remedy")
def shiyi_remedy_route(
    req: RemedyRecallRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """针对模糊或长尾失配查询执行分阶拾遗补救召回。"""
    return remedy_recall_service(
        query=req.query,
        domain=req.domain,
        allow_domain_relax=req.allow_domain_relax,
        limit=req.limit,
        principal=ctx,
    )


@router.post("/shiyi/pipeline")
def shiyi_pipeline_route(
    req: RemedyPipelineRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """主流水线混合检索结合拾遗自适应补偿端点 (ADR-0076)。"""
    return remedy_pipeline_service(
        query=req.query,
        top_k=req.top_k,
        domain=req.domain,
        allow_shiyi=req.allow_shiyi,
        principal=ctx,
    )
