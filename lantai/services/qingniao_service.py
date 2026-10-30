"""青鸟（Qingniao）服务层——多智能体记忆信使与异步通信路由服务 (ADR-0075)。

为 REST 路由、MCP 工具与认知上下文切面提供信函派发、信箱拉取与 ACK 签收服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.qingniao import QingniaoEngine
from lantai.core.auth import Principal
from lantai.storage import db


def dispatch_letter_service(
    *,
    sender_agent_id: str,
    receiver_agent_id: str,
    subject: str,
    content: str,
    priority: str = "normal",
    namespace: str = "default",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """派发青鸟记忆信函。"""
    with db.get_session() as session:
        engine = QingniaoEngine(session=session, principal=principal)
        return engine.dispatch_letter(
            sender_agent_id=sender_agent_id,
            receiver_agent_id=receiver_agent_id,
            subject=subject,
            content=content,
            priority=priority,
            namespace=namespace,
        )


def fetch_inbox_service(
    *,
    receiver_agent_id: str,
    status: str | None = None,
    limit: int = 20,
    namespace: str = "default",
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """拉取智能体收信箱信件。"""
    with db.get_session() as session:
        engine = QingniaoEngine(session=session, principal=principal)
        return engine.fetch_inbox(
            receiver_agent_id=receiver_agent_id,
            status=status,
            limit=limit,
            namespace=namespace,
        )


def acknowledge_letter_service(
    *,
    letter_id: str,
    receiver_agent_id: str,
    action: str = "ack",
    namespace: str = "default",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """对青鸟信函进行收件签收确认或拒收。"""
    with db.get_session() as session:
        engine = QingniaoEngine(session=session, principal=principal)
        return engine.acknowledge_letter(
            letter_id=letter_id,
            receiver_agent_id=receiver_agent_id,
            action=action,
            namespace=namespace,
        )
