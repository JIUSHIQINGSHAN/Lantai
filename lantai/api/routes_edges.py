"""记忆关系 API——薄 handler，逻辑下沉 edge_service"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.services.edge_service import (
    add_edge,
    get_causal_chain,
    get_chain,
    list_edges,
    remove_edge,
)
from lantai.services.provenance_service import (
    evaluate_counterfactual_service,
    get_event_timeline,
    project_causal_impact_service,
    trace_decision_provenance,
)

router = APIRouter()


class EdgeReq(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relation: str
    confidence: float = 0.5


class CounterfactualReq(BaseModel):
    target_memory_id: str
    intervened_node_id: str
    max_depth: int = 10


class ProjectImpactReq(BaseModel):
    memory_id: str
    max_depth: int = 5
    min_confidence: float = 0.05


@router.post("/edges/counterfactual")
def counterfactual_route(req: CounterfactualReq, ctx=Depends(get_current_user)):
    """格物（v0.40）：反事实假设因果判定。"""
    return evaluate_counterfactual_service(
        target_id=req.target_memory_id,
        intervened_node_id=req.intervened_node_id,
        max_depth=req.max_depth,
        principal=ctx,
    )


@router.post("/edges/project-impact")
def project_impact_route(req: ProjectImpactReq, ctx=Depends(get_current_user)):
    """格物（v0.40）：前瞻决策因果影响面模拟。"""
    return project_causal_impact_service(
        memory_id=req.memory_id,
        max_depth=req.max_depth,
        min_confidence=req.min_confidence,
        principal=ctx,
    )


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


@router.get("/edges/provenance")
def decision_provenance_route(
    memory_id: str | None = None,
    query: str | None = None,
    max_depth: int = 10,
    ctx=Depends(get_current_user),
):
    """格物（v0.31）：针对特定决策或状态变更的溯源推理与根因反查。"""
    return trace_decision_provenance(
        memory_id=memory_id, query=query, max_depth=max_depth, principal=ctx
    )


@router.get("/edges/timeline")
def event_timeline_route(
    memory_id: str,
    max_depth: int = 10,
    ctx=Depends(get_current_user),
):
    """格物（v0.31）：生成关联事件的时序演进时间线。"""
    return get_event_timeline(memory_id=memory_id, max_depth=max_depth, principal=ctx)


@router.get("/edges/{memory_id}")
def list_edges_route(memory_id: str, relation: str | None = None, ctx=Depends(get_current_user)):
    # 归属必须下传（票 .scratch/readside-gaps/22）：此前一个身份都不取，
    # `get_edges` 按 source/target 直查全表——A 拿 B 的 memory_id 就能列出
    # B 的每一条边的 id/source/target/relation/confidence。同票 19/20 的
    # 形状：本文件 POST 与 DELETE 都校验了归属，只有这两条读路由漏了。
    return list_edges(memory_id, relation, principal=ctx)


@router.get("/edges/{memory_id}/supersed-chain")
def supersed_chain_route(memory_id: str, ctx=Depends(get_current_user)):
    # 归属必须下传（票 .scratch/readside-gaps/22）：链比边更值钱——它直接
    # 告诉 A「B 的这条记忆被谁取代了」，superseded_by 就是下一步该读的 id。
    return get_chain(memory_id, principal=ctx)


@router.get("/edges/{memory_id}/causal-chain")
def causal_chain_route(
    memory_id: str,
    direction: str = "forward",
    max_depth: int = 10,
    ctx=Depends(get_current_user),
):
    """格物（v0.31）：获取因果拓扑与事件溯源链。"""
    return get_causal_chain(memory_id, direction=direction, max_depth=max_depth, principal=ctx)


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
