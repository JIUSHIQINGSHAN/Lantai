"""记忆关系 API——薄 handler，逻辑下沉 edge_service"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.services.edge_service import add_edge, get_chain, list_edges, remove_edge

router = APIRouter()


class EdgeReq(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relation: str
    confidence: float = 0.5


@router.post("/edges")
def create_edge_route(req: EdgeReq, ctx=Depends(get_current_user)):
    # 归属四元组随 principal 落列（票 .scratch/ownership-gaps/03）：边不带
    # 属主，`DELETE /edges/{id}` 的 ACL 无从校验，supersedes 边改他人召回
    # 排序也无从审计。
    # principal 同时用于事前校验（票 .scratch/ownership-gaps/02）：两端记忆
    # 都必须属于调用者，否则 A 能在 B 的记忆上建 supersedes 边，把 B 的
    # 记忆在 B 自己的检索里压下去（_edge_cb 无用户过滤）。
    return add_edge(
        req.source_memory_id,
        req.target_memory_id,
        req.relation,
        req.confidence,
        user_id=getattr(ctx, "user_id", None),
        tenant_id=getattr(ctx, "tenant_id", None),
        agent_id=getattr(ctx, "agent_id", None),
        session_id=getattr(ctx, "session_id", None),
        principal=ctx,
    )


@router.get("/edges/{memory_id}")
def list_edges_route(memory_id: str, relation: str | None = None):
    return list_edges(memory_id, relation)


@router.get("/edges/{memory_id}/supersed-chain")
def supersed_chain_route(memory_id: str):
    return get_chain(memory_id)


@router.delete("/edges/{edge_id}")
def remove_edge_route(edge_id: str, principal=Depends(get_current_user)):
    """删除记忆关系（含归属校验，P0 票04）"""
    from sqlmodel import select

    from lantai.core.acl import ensure_can_delete
    from lantai.models.tables import MemoryEdge
    from lantai.storage.db import get_session

    with get_session() as s:
        edge = s.exec(select(MemoryEdge).where(MemoryEdge.id == edge_id)).first()
        if edge:
            ensure_can_delete(
                principal, resource_user_id=edge.user_id, resource_tenant_id=edge.tenant_id
            )
    if remove_edge(edge_id):
        return {"ok": True}
    raise HTTPException(404, "edge not found")
