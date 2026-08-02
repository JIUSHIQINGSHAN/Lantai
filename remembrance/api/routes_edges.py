"""记忆关系 API——薄 handler，逻辑下沉 edge_service"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from remembrance.services.edge_service import add_edge, list_edges, get_chain, remove_edge

router = APIRouter()


class EdgeReq(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relation: str
    confidence: float = 0.5


@router.post("/edges")
def create_edge_route(req: EdgeReq):
    return add_edge(req.source_memory_id, req.target_memory_id,
                    req.relation, req.confidence)


@router.get("/edges/{memory_id}")
def list_edges_route(memory_id: str, relation: str | None = None):
    return list_edges(memory_id, relation)


@router.get("/edges/{memory_id}/supersed-chain")
def supersed_chain_route(memory_id: str):
    return get_chain(memory_id)


@router.delete("/edges/{edge_id}")
def remove_edge_route(edge_id: str):
    if remove_edge(edge_id):
        return {"ok": True}
    raise HTTPException(404, "edge not found")
