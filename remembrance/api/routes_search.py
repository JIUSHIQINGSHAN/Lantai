from fastapi import APIRouter
from remembrance.models.schemas import SearchReq
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check

router = APIRouter()

@router.post("/search")
def search(req: SearchReq):
    # Step 1: 启发式闸门预过滤
    gate = relevance_check(req.query)
    if not gate["needs_memory"]:
        return {"results": [], "gate": gate}

    # Step 2: 混合检索
    results = hybrid_search(req.query, req.top_k, req.memory_types,
                           req.lanes, req.use_rerank)
    return {"results": results, "gate": gate}
