"""控制台允许显式运行的 worker 集合与同名互斥。"""
from collections.abc import Callable
from threading import Lock

_LOCKS: dict[str, Lock] = {}


def _runners() -> dict[str, Callable[[], object]]:
    from lantai.evolution.reflector import run_reflect_once
    from lantai.workers.autodream_worker import run_autodream_scheduled
    from lantai.workers.digest_worker import run_candidate_ttl, run_digest_once
    from lantai.workers.evolve_worker import run_evolve_once
    from lantai.workers.forgetting_worker import run_forgetting_once
    from lantai.workers.ingest_worker import run_ingest_once
    from lantai.workers.param_advice_worker import run_param_advice_once

    return {
        "ingest": run_ingest_once,
        "evolve": run_evolve_once,
        "forgetting": run_forgetting_once,
        "candidate_ttl": run_candidate_ttl,
        "digest": run_digest_once,
        "param_advice": run_param_advice_once,
        "reflect": lambda: run_reflect_once(source="manual"),
        "autodream": run_autodream_scheduled,
    }


def run_worker(worker_name: str) -> dict:
    """运行指定 worker；同名任务正在运行时返回冲突。"""
    runner = _runners().get(worker_name)
    if runner is None:
        raise ValueError("worker is not exposed for manual runs")
    lock = _LOCKS.setdefault(worker_name, Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("worker is already running")
    try:
        result = runner()
        return {"ok": True, "worker": worker_name, "result": result}
    finally:
        lock.release()
