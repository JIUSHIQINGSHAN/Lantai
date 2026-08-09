"""Shell Hook: pre_llm_call 时注入相关记忆（零依赖 CLI）。

契约：stdin JSON → stdout {context} 或 {}；2s 硬超时；异常静默降级。"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认 GBK 解码 stdin；Hermes 按 UTF-8 写 JSON，按 GBK 读则中文乱码
# （「你好」→「浣犲ソ」）→ query 检索零命中、注入静默失效。必须在读 stdin 前执行。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remembrance.core.settings import settings
from remembrance.llm.client import embed
from remembrance.storage.vector_store import get_vector_store
from remembrance.storage import db
from remembrance.models.tables import MemoryItem
from sqlmodel import select


def build_context(query: str) -> dict:
    """查询相关记忆，构建注入上下文。

    返回 {"context": ..., "event_id": ...}——event_id 供生成侧回填 used_ids
    （Hermes 若用 shell_hook 通道，回答后按注入的记忆 id 调 backfill）。
    """
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
        evidence = []
        for r in results:
            for m in items:
                if m.id == r["id"]:
                    score = round(1.0 - r["distance"], 2)
                    lines.append(f"- [{score}] {m.content[:200]}")
                    # Ticket 04: 依据段——记忆 id + 内容摘要（可感知、可回填）
                    evidence.append({"id": m.id, "content": m.content[:200],
                                     "score": score})
                    break
        latency_ms = int((time.perf_counter() - t0) * 1000)
        event_id = _try_log(query, [{"score": 1.0 - r["distance"], "memory": {"id": r["id"]}}
                                    for r in results], latency_ms)
        out = {}
        if lines:
            out["context"] = "\n".join(lines)
        if evidence:
            out["context"] = ("【本次依据】\n" +
                              "\n".join(f"- ({e['id']}, score {e['score']}) {e['content']}"
                                         for e in evidence) +
                              "\n\n【相关记忆】\n" + out["context"])
            out["evidence"] = evidence
        if event_id:
            out["event_id"] = event_id
        return out
    except Exception:
        return {}


def _try_log(query: str, results: list, latency_ms: int) -> str | None:
    """Shell Hook 检索埋点（独立向量路径，方向二弱标注源）：失败零侵入。返回 event_id。"""
    try:
        from remembrance.observability.retrieval_log import log_retrieval
        return log_retrieval(query, results, latency_ms=latency_ms,
                             trace_id="shell_hook")
    except Exception:
        return None


def _handle_one(raw: str) -> dict:
    """处理单条请求（单发模式与 serve 模式共用）。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        query = data.get("query", "") or data.get("message", "") or data.get("prompt", "")
    except json.JSONDecodeError:
        return {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(build_context, query)
        try:
            return future.result(timeout=settings.SHELL_HOOK_TIMEOUT)
        except FuturesTimeout:
            return {}
        except Exception:
            return {}


def main():
    if "--serve" in sys.argv:
        # 守护模式：NDJSON 循环，每行一个请求 → 每行一个响应。
        # 常驻进程消除冷启动开销（chromadb/jieba 只加载一次），
        # 供插件通道热调用（serve/桌面模式 Hermes 不跑 shell hooks）。
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            result = _handle_one(line)
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return

    result = _handle_one(sys.stdin.read())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
