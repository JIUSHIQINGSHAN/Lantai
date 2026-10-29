"""目次（Muci）REST 路由端点 (ADR-0065)。

对外暴露记忆知识目录概览与分域专向目次接口。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from lantai.core.auth import get_current_user
from lantai.services.muci_service import get_catalog_service

router = APIRouter(tags=["catalog"])


@router.get("/catalog/overview")
def catalog_overview_route(
    domain: str | None = Query(None, description="限定辨域：user/session/agent"),
    lane: str | None = Query(None, description="限定分轨：fact/rule/preference/experience/general"),
    detail_level: Literal["compact", "full"] = Query(
        "compact", description="目录详尽程度：compact 或 full"
    ),
    max_entries: int = Query(3, ge=1, le=20, description="full 模式下每板块最大条目摘录数"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取记忆全局多维知识目录。"""
    return get_catalog_service(
        principal=ctx,
        domain=domain,
        lane=lane,
        detail_level=detail_level,
        max_entries_per_section=max_entries,
    )


@router.get("/catalog/domains/{domain}")
def catalog_domain_route(
    domain: str,
    lane: str | None = Query(None, description="限定分轨：fact/rule/preference/experience/general"),
    detail_level: Literal["compact", "full"] = Query(
        "compact", description="目录详尽程度：compact 或 full"
    ),
    max_entries: int = Query(3, ge=1, le=20, description="full 模式下每板块最大条目摘录数"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取指定辨域的专向精细目次。"""
    return get_catalog_service(
        principal=ctx,
        domain=domain,
        lane=lane,
        detail_level=detail_level,
        max_entries_per_section=max_entries,
    )
