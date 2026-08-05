from fastapi import APIRouter
from remembrance.models.schemas import SearchReq
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check

router = APIRouter()

@router.post("/search")
def search(req: SearchReq, trace: bool = False, explain: bool = False):
    # Step 1: 启发式闸门预过滤
    gate = relevance_check(req.query)
    if not gate["needs_memory"]:
        # 闸门拦截也算一次观测（zero_result=True 的意义之一）
        _try_log(req, [], 0, gate)
        return {"results": [], "gate": gate}

    # Step 2: 混合检索
    import time
    t0 = time.perf_counter()
    result = hybrid_search(req.query, req.top_k, req.memory_types,
                           req.lanes, req.use_rerank, trace=trace,
                           explain=explain)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    if trace and isinstance(result, tuple):
        results, trace_steps = result
    else:
        results = result
    _try_log(req, results, latency_ms, gate)
    if trace and isinstance(result, tuple):
        return {"results": results, "gate": gate, "trace": trace_steps}
    return {"results": results, "gate": gate}


def _try_log(req, results: list, latency_ms: int, gate: dict) -> None:
    """检索事件埋点（方向二）：失败不影响主链路。"""
    try:
        from remembrance.observability.retrieval_log import log_retrieval
        log_retrieval(req.query, results, latency_ms=latency_ms,
                      gate=gate, lanes=req.lanes)
    except Exception:
        pass  # 埋点必须零侵入
