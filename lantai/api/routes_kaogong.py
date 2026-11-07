"""考功 REST 路由模块（ADR-0078）。

提供：
1. POST /kaogong/eval: 执行全库或指定批次记忆价值演化考评周期；
2. GET /kaogong/report: 获取最新考功审计快照报告；
3. POST /kaogong/inspect: 对单条记忆执行多因子价值深度考查（支持只读与落库）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user, require_admin

router = APIRouter(prefix="/kaogong", tags=["kaogong"])


class KaogongEvalRequest(BaseModel):
    limit: int = Field(default=100, description="最大考评条目数")


class KaogongInspectRequest(BaseModel):
    memory_id: str = Field(description="目标记忆 ID")
    write: bool = Field(default=False, description="是否落库应用升降级修改")


@router.post("/eval")
def kaogong_eval_endpoint(req: KaogongEvalRequest, ctx=Depends(get_current_user)) -> dict[str, Any]:
    """考功：执行记忆价值演化与升降级考评周期（ADR-0078）。"""
    from lantai.services.kaogong_service import run_kaogong_cycle

    return run_kaogong_cycle(principal=ctx, limit=req.limit)


@router.get("/report")
def kaogong_report_endpoint(ctx=Depends(require_admin)) -> dict[str, Any]:
    """考功：获取最新考功评定审计报告快照。

    票 19：require_admin——报告是全局快照，`details[].fenced_content` 携带
    记忆明文全文、`get_report` 不按调用方收窄；此前的身份透传只进 run_cycle
    的空态臂（user 身份空态 GET 会触发**用户视角全表写周期**并把全局快照
    覆写成 user 视角），故收 admin 闸。ctx 透传保持既有形态（admin 空态
    GET 触发 admin 周期是该路由既有契约）。"""
    from lantai.services.kaogong_service import get_kaogong_report

    return get_kaogong_report(principal=ctx)


@router.post("/inspect")
def kaogong_inspect_endpoint(
    req: KaogongInspectRequest, ctx=Depends(get_current_user)
) -> dict[str, Any]:
    """考功：单条记忆多因子价值深度考评（ADR-0078）。"""
    from lantai.services.kaogong_service import kaogong_inspect_service

    try:
        return kaogong_inspect_service(
            memory_id=req.memory_id,
            principal=ctx,
            write=req.write,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except ValueError as ve:
        status = 404 if "not found" in str(ve) or "未找到" in str(ve) else 422
        raise HTTPException(status, str(ve)) from ve
