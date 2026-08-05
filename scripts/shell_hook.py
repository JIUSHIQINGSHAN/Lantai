"""Shell Hook: pre_llm_call 时注入相关记忆（零依赖 CLI）。

契约：stdin JSON → stdout {context} 或 {}；2s 硬超时；异常静默降级。"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remembrance.core.settings import settings
from remembrance.llm.client import embed
from remembrance.storage.vector_store import get_vector_store
from remembrance.storage import db
from remembrance.models.tables import MemoryItem
from sqlmodel import select


def build_context(query: str) -> dict:
    """查询相关记忆，构建注入上下文。"""
    if not query or len(query.strip()) <= settings.SHELL_HOOK_MIN_CHARS:
        return {}

    import time
    try:
        t0 = time.perf_counter()
        qv = embed([query])[0]
        store = get_vector_store()
        results = store.search(qv, top_k=settings.SHELL_HOOK_TOP_K)
        if not results:
            _try_log(query, [], int((time.perf_counter() - t0) * 1000))
            return {}

        ids = [r["id"] for r in results]
        with db.get_session() as s:
            items = s.exec(
                select(MemoryItem)
                .where(MemoryItem.id.in_(ids))
                .where(MemoryItem.status == "active")
            ).all()

        lines = []
        for r in results:
            for m in items:
                if m.id == r["id"]:
                    score = round(1.0 - r["distance"], 2)
                    lines.append(f"- [{score}] {m.content[:200]}")
                    break
        latency_ms = int((time.perf_counter() - t0) * 1000)
        _try_log(query, [{"score": 1.0 - r["distance"], "memory": {"id": r["id"]}}
                         for r in results], latency_ms)
        return {"context": "\n".join(lines)} if lines else {}
    except Exception:
        return {}


def _try_log(query: str, results: list, latency_ms: int) -> None:
    """Shell Hook 检索埋点（独立向量路径，方向二弱标注源）：失败零侵入。"""
    try:
        from remembrance.observability.retrieval_log import log_retrieval
        log_retrieval(query, results, latency_ms=latency_ms,
                      trace_id="shell_hook")
    except Exception:
        pass


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("{}")
        return

    try:
        data = json.loads(raw)
        query = data.get("query", "") or data.get("message", "") or data.get("prompt", "")
    except json.JSONDecodeError:
        print("{}")
        return

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(build_context, query)
        try:
            result = future.result(timeout=settings.SHELL_HOOK_TIMEOUT)
        except FuturesTimeout:
            print("{}")
            os._exit(0)  # 硬退出：不等滞留线程，保证宿主不被拖慢
        except Exception:
            result = {}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
