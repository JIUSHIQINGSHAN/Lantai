from fastapi import APIRouter, Depends
from lantai.core.acl import allowed_lanes, filter_results_by_lanes, verify_agent
from lantai.models.schemas import SearchReq
from lantai.retrieval.hybrid import hybrid_search
from lantai.gate.prefilter import relevance_check

router = APIRouter()

@router.post("/search")
def search(req: SearchReq, trace: bool = False, explain: bool = False,
          agent_id: str = Depends(verify_agent)):
    # Step 1: 启发式闸门预过滤（拾遗 ADR-0028：支持 req.force 透传放行）
    gate = relevance_check(req.query)
    if not req.force and not gate["needs_memory"]:
        # 闸门拦截也算一次观测（zero_result=True 的意义之一）
        event_id = _try_log(req, [], 0, gate)
        return {"results": [], "gate": gate, "event_id": event_id}

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
    # ACL：检索结果按绑定 lane 集收窄（宁 miss 不放行未绑定 lane）
    results = filter_results_by_lanes(results, allowed_lanes(agent_id))
    event_id = _try_log(req, results, latency_ms, gate)
    from lantai.retrieval.evidence import build_evidence
    evidence = build_evidence(results)
    if trace and isinstance(result, tuple):
        return {"results": results, "gate": gate, "trace": trace_steps,
                "event_id": event_id, "evidence": evidence}
    return {"results": results, "gate": gate, "event_id": event_id,
            "evidence": evidence}


def _try_log(req, results: list, latency_ms: int, gate: dict) -> str | None:
    """检索事件埋点（方向二）：失败不影响主链路。返回 event_id 供生成侧回填。"""
    try:
        from lantai.observability.retrieval_log import log_retrieval
        return log_retrieval(req.query, results, latency_ms=latency_ms,
                             gate=gate, lanes=req.lanes)
    except Exception:
        return None  # 埋点必须零侵入
