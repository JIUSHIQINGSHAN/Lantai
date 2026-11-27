"""拾遗（Shiyi）服务层——遗漏补偿与零召回自适应补救服务 (ADR-0069)。

为 REST 路由、MCP 工具与认知上下文切面提供分阶自适应补救服务。
"""

from __future__ import annotations

from typing import Any, cast

from lantai.cognition.shiyi import ShiyiEngine
from lantai.core.acl import Principal
from lantai.storage import db


def _converge(principal):
    """匿名外部面收敛到 default 属主（2026-11-24）。

    `ShiyiEngine._check_access` 对 `principal=None` 全放，那是**内部调用**
    的契约（worker/CLI/直调不空转，`tests/test_key_gate_and_shiyi_perf.py`
    钉住）。而本模块是 MCP `shiyi_remedy` / `shiyi_pipeline` 与 REST 的入口
    面：MCP 宿主不透传 `user_id` 时 `_principal_from_params` 返回 None，
    全放等于匿名调用方拿到全库模糊召回（候选带完整 `content`）。

    不动承重墙、在调用方收敛——与 `hybrid_search._converge_principal` 同一
    范式（该函数 docstring 记了四次重复写同一段收敛的由来）。admin /
    `SYSTEM_VIEWER` 见过的 `principal` 原样传下去，全表口径不搬家。
    """
    if principal is not None:
        return principal
    return Principal(role="user", user_id="default")


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
        engine = ShiyiEngine(session=session, principal=_converge(principal))
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
        results = cast(
            list[dict[str, Any]],
            hybrid_search(
                query=query,
                top_k=top_k,
                domain=domain,
                session=session,
                principal=_converge(principal),
                enable_shiyi=allow_shiyi,
            ),
        )
        is_remedied = any(r.get("is_remedied", False) for r in results)
        return {
            "query": query,
            "total": len(results),
            "is_remedied": is_remedied,
            "results": results,
        }
