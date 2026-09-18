"""lane and domain ACL (TencentDB Memory Hub Fixed Binding + ACL)."""

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from lantai.core.settings import settings


@dataclass(frozen=True)
class Principal:
    """The identity and authorization context of the current requester."""

    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    role: str = "user"  # "admin", "system", "user"
    allowed_lanes: list[str] | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "system")


# Global fallback for unauthenticated requests / legacy codebase
SYSTEM_PRINCIPAL = Principal(role="system")


def active_bindings() -> dict:
    return dict(settings.AGENT_LANE_BINDINGS or {})


def allowed_lanes(agent_id: str) -> list[str] | None:
    bindings = active_bindings()
    if not bindings:
        return None
    return bindings.get(agent_id) or []


def lane_allowed(agent_id: str, lane: str) -> bool:
    lanes = allowed_lanes(agent_id)
    if lanes is None:
        return True
    return lane in lanes


def filter_results_by_lanes(results: list, lanes: list[str] | None) -> list:
    if lanes is None:
        return results
    allowed = set(lanes)

    def _lane(r) -> str:
        if isinstance(r, dict) and isinstance(r.get("memory"), dict):
            return r["memory"].get("lane") or settings.RAW_MEMORY_DEFAULT_LANE
        return settings.RAW_MEMORY_DEFAULT_LANE

    return [r for r in results if _lane(r) in allowed]


def ensure_can_delete(
    principal,
    *,
    resource_user_id: str | None,
    resource_tenant_id: str | None = None,
    lane: str | None = None,
) -> None:
    """删除等破坏性操作的资源归属校验（P0 票04）。

    admin/system 全权。非 admin 要求：
    - lane 在主体 allowed_lanes 内（agent 绑定生效时以绑定 lanes 为准）
    - 租户匹配（资源与主体都非空时）
    - 用户匹配：资源标了 user_id 且与主体不同 → 403；资源无归属
      （user_id 为空，多为 v022 之前的历史行）→ 不视为越权，只受 lane 约束
    违规统一 403（不区分 404，避免资源存在性探测）。
    """
    if getattr(principal, "is_admin", False):
        return
    agent_id = getattr(principal, "agent_id", None)
    lanes = getattr(principal, "allowed_lanes", None)
    if agent_id and active_bindings():
        lanes = allowed_lanes(agent_id) or []
    if lane is not None and lanes is not None and lane not in lanes:
        raise HTTPException(status_code=403, detail="lane not allowed for this principal")
    p_tenant = getattr(principal, "tenant_id", None)
    if resource_tenant_id and p_tenant and resource_tenant_id != p_tenant:
        raise HTTPException(status_code=403, detail="resource belongs to another tenant")
    p_user = getattr(principal, "user_id", None)
    if resource_user_id and p_user and resource_user_id != p_user:
        raise HTTPException(status_code=403, detail="resource belongs to another user")


def verify_agent(
    x_agent_id: str | None = Header(None, alias="X-Agent-Id"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
) -> Principal:
    if not active_bindings():
        return Principal(
            tenant_id=x_tenant_id,
            user_id=x_user_id,
            agent_id=x_agent_id,
            session_id=x_session_id,
            allowed_lanes=None,
        )
    if not x_agent_id or x_agent_id not in active_bindings():
        raise HTTPException(status_code=403, detail="Agent not bound (ACL)")

    return Principal(
        tenant_id=x_tenant_id,
        user_id=x_user_id,
        agent_id=x_agent_id,
        session_id=x_session_id,
        allowed_lanes=allowed_lanes(x_agent_id),
    )
