"""石室（Shishi）服务层——高密核心记忆金匮封存与防篡改硬核锚定服务 (ADR-0074)。

为 REST 路由、MCP 工具与认知上下文切面提供封存更新、完整性验真与封存块检索服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.shishi import ShishiEngine
from lantai.core.auth import Principal
from lantai.storage import db


def seal_core_memory_service(
    *,
    block: str,
    content: str,
    is_immutable: bool = True,
    namespace: str = "default",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """金匮封存或更新核心记忆块。"""
    with db.get_session() as session:
        engine = ShishiEngine(session=session, principal=principal)
        return engine.seal_core_memory(
            block=block,
            content=content,
            is_immutable=is_immutable,
            namespace=namespace,
        )


def verify_shishi_integrity_service(
    *,
    namespace: str = "default",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """对指定命名空间下的核心记忆进行防篡改完整性验真。"""
    with db.get_session() as session:
        engine = ShishiEngine(session=session, principal=principal)
        return engine.verify_integrity(namespace=namespace)


def get_sealed_blocks_service(
    *,
    namespace: str = "default",
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """读取已封存的核心记忆块（强制樊篱包裹）。"""
    with db.get_session() as session:
        engine = ShishiEngine(session=session, principal=principal)
        return engine.get_sealed_blocks(namespace=namespace)
