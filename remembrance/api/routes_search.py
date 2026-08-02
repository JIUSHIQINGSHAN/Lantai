from fastapi import APIRouter
from remembrance.models.schemas import SearchReq
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check

router = APIRouter()

@router.post("/search")
def search(req: SearchReq, trace: bool = False):
    # Step 1: 启发式闸门预过滤
    gate = relevance_check(req.query)
    if not gate["needs_memory"]:
        return {"results": [], "gate": gate}

    # Step 2: 混合检索
    result = hybrid_search(req.query, req.top_k, req.memory_types,
                           req.lanes, req.use_rerank, trace=trace)
    if trace and isinstance(result, tuple):
        results, trace_steps = result
        return {"results": results, "gate": gate, "trace": trace_steps}
    return {"results": result, "gate": gate}
