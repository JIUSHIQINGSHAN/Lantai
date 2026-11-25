"""同袍分层存储：私有读写、公共典籍只读快照，不参与技能晋升。"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlmodel import Session, select

from lantai.core.acl import Principal
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import WorkspaceMemory
from lantai.security.injection_guard import assert_no_prompt_injection
from lantai.storage import db

Scope = Literal["private", "swarm_shared", "all"]


@dataclass(frozen=True)
class MemoryView:
    """只含不可变值的挂载快照，不能经 ORM 会话回写公共典籍。"""

    id: str
    content: str
    shared_scope: str
    tenant_id: str
    user_id: str
    agent_id: str
    lane: str
    created_at: datetime


def _identity(principal: Principal) -> tuple[str, str, str]:
    values = tuple(getattr(principal, name, None) for name in ("tenant_id", "user_id", "agent_id"))
    if not all(isinstance(value, str) and value.strip() == value and value for value in values):
        raise HTTPException(403, "Explicit tenant, user and agent identity required")
    return values


def _view(memory: WorkspaceMemory) -> MemoryView:
    return MemoryView(
        id=memory.id,
        content=memory.content,
        shared_scope=memory.shared_scope,
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        agent_id=memory.agent_id,
        lane=memory.lane,
        created_at=memory.created_at,
    )


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
    else:
        with db.get_session() as current:
            yield current


def _store(
    content: str,
    *,
    principal: Principal,
    shared_scope: Literal["private", "swarm_shared"],
    lane: str,
    session: Session | None,
) -> MemoryView:
    tenant_id, user_id, agent_id = _identity(principal)
    if principal.allowed_lanes is not None and lane not in principal.allowed_lanes:
        raise HTTPException(403, "lane not allowed for this principal")
    if not isinstance(lane, str) or not lane.strip() or lane.strip() != lane:
        raise ValueError("lane must be non-empty")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be non-empty")
    assert_no_prompt_injection(content)
    memory = WorkspaceMemory(
        id=new_id("mem"),
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        content=content,
        lane=lane,
        shared_scope=shared_scope,
    )
    with _session_scope(session) as current:
        current.add(memory)
        current.commit()
        current.refresh(memory)
        return _view(memory)


def store_private_memory(
    content: str,
    *,
    principal: Principal,
    lane: str = "general",
    session: Session | None = None,
) -> MemoryView:
    """身份只取可信 Principal，默认私有，不进入旧接口的全局索引。"""
    return _store(content, principal=principal, shared_scope="private", lane=lane, session=session)


def publish_shared_memory(
    content: str,
    *,
    publisher: Principal,
    workspace: Principal,
    lane: str = "general",
    session: Session | None = None,
) -> MemoryView:
    """仅管理身份可向指定用户空间发布；普通 Agent 的挂载不授予写权限。"""
    if not getattr(publisher, "is_admin", False):
        raise HTTPException(403, "Shared knowledge publication requires admin privileges")
    return _store(
        content, principal=workspace, shared_scope="swarm_shared", lane=lane, session=session
    )


def mount_memories(
    *,
    principal: Principal,
    shared_scope: Scope = "all",
    query: str | None = None,
    limit: int = 100,
    session: Session | None = None,
) -> tuple[MemoryView, ...]:
    """先在数据库按完整归属过滤，再查询、限量，返回只读快照。"""
    tenant_id, user_id, agent_id = _identity(principal)
    if shared_scope not in ("private", "swarm_shared", "all"):
        raise ValueError("Invalid shared_scope")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be in 1..1000")
    visible = (WorkspaceMemory.shared_scope == "swarm_shared") | (
        (WorkspaceMemory.shared_scope == "private") & (WorkspaceMemory.agent_id == agent_id)
    )
    statement = select(WorkspaceMemory).where(
        WorkspaceMemory.status == "active",
        WorkspaceMemory.tenant_id == tenant_id,
        WorkspaceMemory.user_id == user_id,
        visible,
    )
    if principal.allowed_lanes is not None:
        statement = statement.where(WorkspaceMemory.lane.in_(principal.allowed_lanes))
    if shared_scope != "all":
        statement = statement.where(WorkspaceMemory.shared_scope == shared_scope)
    if query:
        statement = statement.where(WorkspaceMemory.content.contains(query, autoescape=True))
    with _session_scope(session) as current:
        rows = current.exec(
            statement.order_by(WorkspaceMemory.created_at.desc(), WorkspaceMemory.id).limit(limit)
        ).all()
        return tuple(_view(row) for row in rows)
