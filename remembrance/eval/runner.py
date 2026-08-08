"""评估运行器：遍历查询集跑 hybrid_search，汇总指标，写 EvalRun。

契约见 docs/dry-run-eval-task-split.md。
"""
import time

from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.core.logger import logger
from remembrance.core.time import utcnow
from remembrance.eval.models import EvalQuerySet, EvalRun
from remembrance.eval.metrics import compute_metrics
from remembrance.eval.query_set import load_query_set
from remembrance.models.tables import RetrievalEvent
from remembrance.parameters.registry import default_snapshot
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.storage import db


def _load_used_ids_map(per_query: list[dict]) -> dict[str, list[str]]:
    """按 event_id 批量拉 retrieval_event.used_ids，构造 {event_id: [used_id, ...]}。

    无任何回填时返回空 dict → weak_hit_rate 诚实标 None。
    只查 per_query 里出现的 event_id，避免全表扫描。
    """
    event_ids = [q.get("event_id") or "" for q in per_query]
    event_ids = [e for e in event_ids if e]
    if not event_ids:
        return {}
    used_map: dict[str, list[str]] = {}
    with db.get_session() as s:
        rows = s.exec(select(RetrievalEvent).where(RetrievalEvent.id.in_(event_ids))).all()
    for ev in rows:
        used = ev.used_ids or []
        if used:
            used_map[ev.id] = [str(u) for u in used]
    return used_map


def run_dry_run(query_set: EvalQuerySet, *, param_overrides: dict | None = None,
                top_k: int = 5, baseline_run_id: str | None = None,
                use_rerank: bool = True, intent_mode: str = "llm") -> EvalRun:
    """遍历查询集调 hybrid_search，算指标，写 EvalRun（status=done）。

    单条查询失败不中断：记录该条 error 继续，metrics 带 errors 计数。

    intent_mode:
        "llm"（默认）— 真实 LLM 意图分类（评估真实行为）
        "rule"         — 跳过 LLM，用 DEFAULT_INTENT（评估管道快速跑；
                          LLM API 慢/不可用时用，不影响召回指标）
    """
    from unittest.mock import patch as _patch

    run_id = new_id("erun")
    effective_snapshot = default_snapshot()
    if param_overrides:
        effective_snapshot.update({k: float(v) for k, v in param_overrides.items()})

    per_query: list[dict] = []
    errors = 0
    queries = query_set.queries or []

    # intent_mode="rule" 时替换 classify_intent（仅本调用内生效）
    _intent_patcher = None
    if intent_mode == "rule":
        from remembrance.core.settings import settings as _s
        _intent_patcher = _patch(
            "remembrance.retrieval.hybrid.classify_intent",
            return_value={"intent": _s.DEFAULT_INTENT,
                          "candidate_n": _s.INTENT_CANDIDATE_SIZES.get(_s.DEFAULT_INTENT, 10)})
        _intent_patcher.start()

    for i, q in enumerate(queries):
        query_text = (q or {}).get("query", "")
        if not query_text:
            errors += 1
            per_query.append({"query": "", "event_id": (q or {}).get("event_id", ""),
                              "result_ids": [], "top_scores": [],
                              "zero_result": True, "latency_ms": 0, "error": "empty_query"})
            continue
        t0 = time.perf_counter()
        try:
            results = hybrid_search(
                query_text, top_k=top_k,
                use_rerank=use_rerank,
                param_overrides=param_overrides,
            )
            if isinstance(results, tuple):  # trace=True 不可能在这里，防御
                results = results[0]
            latency_ms = int((time.perf_counter() - t0) * 1000)
            ids = []
            scores = []
            for r in results or []:
                if isinstance(r, dict):
                    if "memory" in r:
                        m = r["memory"]
                        if isinstance(m, dict) and m.get("id"):
                            ids.append(m["id"])
                        elif hasattr(m, "id"):
                            ids.append(m.id)
                    elif "document" in r:
                        # rerank 结果无 memory.id，用 document 截断代替
                        ids.append(f"doc:{r['document'][:40]}")
                    if "score" in r:
                        scores.append(round(float(r["score"]), 4))
            per_query.append({
                "query": query_text,
                "event_id": (q or {}).get("event_id", ""),
                "result_ids": ids,
                "top_scores": scores,
                "zero_result": not ids,
                "latency_ms": latency_ms,
            })
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整个 dry-run
            errors += 1
            logger.warning("dry-run query %d failed: %s", i, exc)
            per_query.append({
                "query": query_text, "result_ids": [], "top_scores": [],
                "zero_result": True, "latency_ms": 0, "error": str(exc)[:200],
            })

    # 基线 per_query 用于 jaccard（按 queries 顺序对齐）
    baseline_per_query = None
    if baseline_run_id:
        with db.get_session() as s:
            base = s.get(EvalRun, baseline_run_id)
            if base and base.per_query:
                baseline_per_query = [pq.get("result_ids") or []
                                      for pq in base.per_query]

    if _intent_patcher is not None:
        _intent_patcher.stop()

    # used_ids 弱标注回填（方向二）：按 event_id 从 retrieval_event 拉 used_ids，
    # 无回填时 used_ids_map 为空 → weak_hit_rate 诚实标 None（不编造 0）。
    used_ids_map = _load_used_ids_map(per_query)
    metrics = compute_metrics(per_query, baseline_per_query=baseline_per_query,
                              used_ids_map=used_ids_map)
    if errors:
        metrics["errors"] = errors

    run = EvalRun(
        id=run_id,
        query_set_id=query_set.id,
        query_set_name=query_set.name,
        param_overrides={k: float(v) for k, v in (param_overrides or {}).items()},
        param_snapshot=effective_snapshot,
        status="done",
        finished_at=utcnow(),
        metrics=metrics,
        per_query=per_query,
        baseline_run_id=baseline_run_id,
    )
    with db.get_session() as s:
        s.add(run)
        s.commit()
        s.refresh(run)

    logger.info("dry-run done: run=%s set=%s samples=%d errors=%d metrics=%s",
                run_id, query_set.name, len(per_query), errors, metrics)
    return run


def load_run(run_id: str):
    """按 id 加载 EvalRun（供 CLI/集成用）。"""
    with db.get_session() as s:
        return s.get(EvalRun, run_id)


def list_runs(query_set_name: str | None = None, limit: int = 20) -> list[EvalRun]:
    """列出最近运行（可选按查询集过滤）。"""
    with db.get_session() as s:
        stmt = select(EvalRun).order_by(EvalRun.started_at.desc())
        if query_set_name:
            stmt = stmt.where(EvalRun.query_set_name == query_set_name)
        stmt = stmt.limit(limit)
        return s.exec(stmt).all()
