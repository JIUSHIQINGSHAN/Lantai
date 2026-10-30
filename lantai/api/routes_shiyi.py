"""拾遗（Shiyi）REST 路由端点 (ADR-0069)。

对外暴露遗漏记忆自适应补救召回接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.shiyi_service import remedy_recall_service

router = APIRouter(tags=["shiyi"])


class RemedyRecallRequest(BaseModel):
    query: str = Field(..., description="待执行补救召回的查询文本")
    domain: str = Field("all", description="限定辨域：user/session/agent/all")
    allow_domain_relax: bool = Field(False, description="首轮失配时是否允许放宽辨域")
    limit: int = Field(5, ge=1, le=20, description="最大返回补救条数")


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
