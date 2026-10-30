"""青鸟（Qingniao）REST 路由端点 (ADR-0075)。

对外暴露多智能体记忆信使派发、信箱拉取与 ACK 签收接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.services.qingniao_service import (
    acknowledge_letter_service,
    dispatch_letter_service,
    fetch_inbox_service,
)

router = APIRouter(tags=["qingniao"])


class DispatchLetterRequest(BaseModel):
    sender_agent_id: str = Field(..., description="发信智能体标识")
    receiver_agent_id: str = Field(..., description="收信智能体标识（* 为广播）")
    subject: str = Field(..., min_length=1, description="信函主题")
    content: str = Field(..., min_length=1, description="记忆内容正文")
    priority: str = Field("normal", description="优先级：high | normal | low")
    namespace: str = Field("default", description="命名空间")


class AcknowledgeLetterRequest(BaseModel):
    letter_id: str = Field(..., description="信函标识")
    receiver_agent_id: str = Field(..., description="签收智能体标识")
    action: str = Field("ack", description="处理动作：ack | reject")
    namespace: str = Field("default", description="命名空间")


@router.post("/qingniao/dispatch")
def qingniao_dispatch_route(
    req: DispatchLetterRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """派发青鸟记忆信函。"""
    return dispatch_letter_service(
        sender_agent_id=req.sender_agent_id,
        receiver_agent_id=req.receiver_agent_id,
        subject=req.subject,
        content=req.content,
        priority=req.priority,
        namespace=req.namespace,
        principal=ctx,
    )


@router.get("/qingniao/inbox")
def qingniao_inbox_route(
    receiver_agent_id: str = Query(..., description="接收智能体标识"),
    status: str | None = Query(None, description="状态过滤"),
    limit: int = Query(20, ge=1, le=100, description="返回数量上限"),
    namespace: str = Query("default", description="命名空间"),
    ctx=Depends(get_current_user),
) -> list[dict[str, Any]]:
    """拉取指定智能体的收信箱信件。"""
    return fetch_inbox_service(
        receiver_agent_id=receiver_agent_id,
        status=status,
        limit=limit,
        namespace=namespace,
        principal=ctx,
    )


@router.post("/qingniao/ack")
def qingniao_ack_route(
    req: AcknowledgeLetterRequest,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """签收或拒收青鸟信函。"""
    return acknowledge_letter_service(
        letter_id=req.letter_id,
        receiver_agent_id=req.receiver_agent_id,
        action=req.action,
        namespace=req.namespace,
        principal=ctx,
    )
