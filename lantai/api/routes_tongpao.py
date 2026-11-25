"""同袍工作区接口，身份来自绑定密钥，公共典籍挂载只读。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from lantai.core.acl import Principal
from lantai.core.auth import Principal as AuthPrincipal
from lantai.core.auth import get_current_user, require_admin
from lantai.services.tongpao_workspace import (
    MemoryView,
    Scope,
    mount_memories,
    publish_shared_memory,
    store_private_memory,
)

router = APIRouter(prefix="/tongpao", tags=["tongpao"])


class PrivateMemoryReq(BaseModel):
    """私有正文与泳道；归属不可由请求体提供。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=50_000)
    lane: str = Field(default="general", min_length=1, max_length=128)


class SharedMemoryReq(PrivateMemoryReq):
    """管理员发布到显式指定的工作区。"""

    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)


def require_workspace(principal: AuthPrincipal = Depends(get_current_user)) -> AuthPrincipal:
    """仅允许持有完整身份绑定密钥的调用者进入工作区。"""
    if not principal.workspace_bound:
        raise HTTPException(403, "A tenant and agent bound API key is required")
    return principal


@router.post("/private")
def write_private(
    req: PrivateMemoryReq, principal: AuthPrincipal = Depends(require_workspace)
) -> MemoryView:
    """保存当前 Agent 的私有记忆。"""
    try:
        return store_private_memory(req.content, lane=req.lane, principal=principal)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/memories")
def mount_workspace(
    shared_scope: Scope = "all",
    query: str | None = Query(default=None, max_length=8000),
    limit: int = Query(default=100, ge=1, le=1000),
    principal: AuthPrincipal = Depends(require_workspace),
) -> dict[str, tuple[MemoryView, ...]]:
    """只读挂载自身私有记忆及同空间公共典籍。"""
    return {
        "memories": mount_memories(
            principal=principal, shared_scope=shared_scope, query=query, limit=limit
        )
    }


@router.post("/shared")
def publish_shared(
    req: SharedMemoryReq, publisher: AuthPrincipal = Depends(require_admin)
) -> MemoryView:
    """经管理员鉴权后发布公共典籍。"""
    workspace = Principal(tenant_id=req.tenant_id, user_id=req.user_id, agent_id=req.agent_id)
    try:
        return publish_shared_memory(
            req.content, lane=req.lane, publisher=publisher, workspace=workspace
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
