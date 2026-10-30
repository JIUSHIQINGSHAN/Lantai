"""拾遗（Shiyi）服务层——遗漏补偿与零召回自适应补救服务 (ADR-0069)。

为 REST 路由、MCP 工具与认知上下文切面提供分阶自适应补救服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.shiyi import ShiyiEngine
from lantai.core.auth import Principal
from lantai.storage import db


def remedy_recall_service(
    *,
    query: str,
    domain: str = "all",
    allow_domain_relax: bool = False,
    limit: int = 5,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """针对检索查询启动分阶拾遗补救服务。"""
    with db.get_session() as session:
        engine = ShiyiEngine(session=session, principal=principal)
        return engine.remedy_recall(
            query=query,
            domain=domain,
            allow_domain_relax=allow_domain_relax,
            limit=limit,
        )


def remedy_pipeline_service(
    *,
    query: str,
    top_k: int = 5,
    domain: str | None = None,
    allow_shiyi: bool = True,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """主流水线混合检索结合拾遗自适应补偿的服务层封装 (ADR-0076)。"""
    from lantai.retrieval.hybrid import hybrid_search

    with db.get_session() as session:
        results = hybrid_search(
            query=query,
            top_k=top_k,
            domain=domain,
            session=session,
            principal=principal,
            enable_shiyi=allow_shiyi,
        )
        is_remedied = any(r.get("is_remedied", False) for r in results)
        return {
            "query": query,
            "total": len(results),
            "is_remedied": is_remedied,
            "results": results,
        }
