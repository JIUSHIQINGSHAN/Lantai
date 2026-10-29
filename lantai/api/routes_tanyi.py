"""探颐（v0.38 Ticket 02）：存储驱动主动探针 REST 路由。

归属口径（同 .scratch/readside-gaps/07 与 probing_service 的既有决定）：
- 探针会把别人的冲突正文渲染成"顺便向您求证确认下：关于「…」"的问句，
  那是比列表更直接的泄漏面，因此从严收窄。
- 非 admin：tenant_id + user_id 双重过滤（同租户跨属主零泄漏），
  NULL 属主老行不可见（宁可漏探，不可错探——宁 miss 不脏写）。
- admin/system 全权：不限租户与属主。

校验在引擎层（lantai/cognition/tanyi.py），这里只负责把 principal 传下去。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lantai.cognition.tanyi import TanyiEngine
from lantai.core.auth import get_current_user

router = APIRouter(prefix="/cognition", tags=["cognition"])


class TanyiProbeReq(BaseModel):
    query: str | None = Field(default=None, description="可选查询词，过滤相关探针")
    limit: int = Field(default=10, ge=1, le=100, description="探针数量上限")


def _probe_scope(principal) -> dict[str, str | None]:
    """解析探针扫描的归属范围：非 admin 收敛到自身租户与属主（宁 miss 不脏写）。

    与 ContextBuilder._owned_memory_stmt 口径分歧的说明：探针会把正文
    渲染成问句直接送达用户，是比上下文注入更直接的泄漏面，因此 NULL
    属主老行一律不可见（同 probing_service._visible 的从严口径）。
    """
    if principal is not None and bool(getattr(principal, "is_admin", False)):
        return {"tenant_id": None, "user_id": None}
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return {
        "tenant_id": getattr(principal, "tenant_id", None),
        "user_id": viewer,
    }


@router.post("/tanyi/probe")
def tanyi_probe_route(req: TanyiProbeReq, ctx=Depends(get_current_user)) -> dict[str, Any]:
    """探颐：主动扫描归属范围内未决冲突与低置信度事实，输出探针与追问建议。"""
    from lantai.storage import db

    scope = _probe_scope(ctx)
    with db.get_session() as s:
        engine = TanyiEngine(storage=s)
        probes = engine.probe_ambiguities(
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            query=req.query,
            limit=req.limit,
        )
        return {
            "probes": [p.__dict__ for p in probes],
            "clarification_prompts": engine.generate_clarification_prompts(probes),
            "scope": scope,
        }
