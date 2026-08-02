"""记忆关系 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from remembrance.storage.edges import create_edge, get_edges, delete_edge, get_supersed_chain

router = APIRouter()


class EdgeReq(BaseModel):
    source_memory_id: str
    target_memory_id: str
    relation: str
    confidence: float = 0.5


@router.post("/edges")
def add_edge(req: EdgeReq):
    edge = create_edge(req.source_memory_id, req.target_memory_id,
                       req.relation, req.confidence)
    return {"edge_id": edge.id, "relation": edge.relation}


@router.get("/edges/{memory_id}")
def list_edges(memory_id: str, relation: str | None = None):
    edges = get_edges(memory_id, relation=relation)
    return {"edges": [
        {
            "id": e.id,
            "source": e.source_memory_id,
            "target": e.target_memory_id,
            "relation": e.relation,
            "confidence": e.confidence,
        }
        for e in edges
    ]}


@router.get("/edges/{memory_id}/supersed-chain")
def supersed_chain(memory_id: str):
    chain = get_supersed_chain(memory_id)
    return {"chain": chain}


@router.delete("/edges/{edge_id}")
def remove_edge(edge_id: str):
    if delete_edge(edge_id):
        return {"ok": True}
    raise HTTPException(404, "edge not found")
