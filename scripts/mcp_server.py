"""MCP Server——标准协议写操作（search/add/feedback）

与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）
标准 MCP JSON-RPC 2.0 协议
"""
import json
import os
import sys

# 使子进程无论 cwd 在哪都能 import remembrance（Hermes 拉 MCP 时 cwd 不可控）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认用 GBK 解码 stdin/stdout；Hermes 按 UTF-8 写 JSON（含中文 query），
# 若按 GBK 读则中文全变乱码（如「你好」→「浣犲ソ」）→ 检索零命中、注入全失效。
# 必须在任何 stdin/stdout 读写前执行。Python 3.7+ reconfigure；旧版靠环境变量兜底。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from pydantic import ValidationError

from remembrance.models.schemas import AddMemoryReq, SearchReq, FeedbackReq
from remembrance.services.memory_service import add_memory
from remembrance.services.evolution_service import record_feedback_entry
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check

PROTOCOL_VERSION = "2024-11-05"


def handle_search(params: dict) -> dict:
    query = params.get("query", "")
    if not isinstance(query, str) or not (1 <= len(query) <= 8000):
        raise ValueError("query must be a string of 1..8000 chars")
    top_k = params.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not (1 <= top_k <= 100):
        raise ValueError("top_k must be an int in [1, 100]")
    gate = relevance_check(query)
    if not gate["needs_memory"]:
        event_id = _try_log(query, [], 0, gate)
        return {"results": [], "gate": gate, "event_id": event_id}
    import time
    t0 = time.perf_counter()
    results = hybrid_search(query, top_k=top_k)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    event_id = _try_log(query, results, latency_ms, gate)
    return {"results": results, "gate": gate, "event_id": event_id}


def _try_log(query: str, results: list, latency_ms: int, gate: dict) -> str | None:
    """检索事件埋点（方向二）：失败零侵入。返回 event_id 供生成侧回填 used_ids。"""
    try:
        from remembrance.observability.retrieval_log import log_retrieval
        return log_retrieval(query, results, latency_ms=latency_ms, gate=gate)
    except Exception:
        return None


def handle_backfill(params: dict) -> dict:
    """生成侧回填：Hermes 回答时实际用到的记忆 id 写回检索事件（弱标注）。"""
    event_id = params.get("event_id", "")
    used_ids = params.get("used_ids", [])
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if not isinstance(used_ids, list) or not all(isinstance(x, str) for x in used_ids):
        raise ValueError("used_ids must be a list of strings")
    from remembrance.observability.retrieval_log import backfill_used_ids as _bf
    _bf(event_id, used_ids)
    return {"ok": True, "event_id": event_id, "used_count": len(used_ids)}


def handle_add(params: dict) -> dict:
    req = AddMemoryReq(
        title=params.get("title", ""),
        content=params.get("content", ""),
        lane=params.get("lane", "general"),
    )
    return add_memory(req)


def handle_add_dialogue(params: dict) -> dict:
    """对话写通道：对话文本 → 提取链（fastpath 直通 / 候选 / 闲聊入队）。"""
    text = params.get("text", "")
    user_id = params.get("user_id", "default")
    source = params.get("source", "dialogue")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    from remembrance.ingestion.dialogue import ingest_dialogue
    return ingest_dialogue(text, user_id=user_id, source=source)


def handle_candidates_pending(params: dict) -> dict:
    """待审候选列表（Ticket 02）——被闸门拒绝的候选进此队列等人工裁决。"""
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from remembrance.services.candidate_service import list_pending_candidates
    return list_pending_candidates(limit)


def handle_candidate_review(params: dict) -> dict:
    """审核候选：approve → 进提案链并应用；reject → 归档。"""
    candidate_id = params.get("candidate_id", "")
    approve = params.get("approve", False)
    reason = params.get("reason", "")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be a non-empty string")
    if not isinstance(approve, bool):
        raise ValueError("approve must be a boolean")
    from remembrance.services.candidate_service import review_candidate
    return review_candidate(candidate_id, approve=approve, reason=reason)


def handle_feedback(params: dict) -> dict:
    req = FeedbackReq(
        memory_id=params.get("memory_id", ""),
        query=params.get("query", ""),
        helped=params.get("helped", False),
        user_accepted=params.get("user_accepted", False),
    )
    return record_feedback_entry(req)


TOOLS = {
    "search":   {"description": "搜索记忆", "inputSchema": {
        "type": "object", "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "top_k": {"type": "integer", "default": 5},
        }, "required": ["query"]}},
    "add":      {"description": "添加记忆", "inputSchema": {
        "type": "object", "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "lane": {"type": "string", "default": "general"},
        }, "required": ["content"]}},
    "feedback": {"description": "反馈记忆有用性", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string"},
            "query": {"type": "string"},
            "helped": {"type": "boolean"},
            "user_accepted": {"type": "boolean"},
        }, "required": ["memory_id"]}},
    "backfill": {"description": "回填弱标注：回答时实际用到的记忆 id 写回检索事件", "inputSchema": {
        "type": "object", "properties": {
            "event_id": {"type": "string", "description": "search 返回的检索事件 id"},
            "used_ids": {"type": "array", "items": {"type": "string"},
                         "description": "实际用进回答的记忆 id 列表"},
        }, "required": ["event_id", "used_ids"]}},
    "add_dialogue": {"description": "对话写通道：提交对话文本，自动提炼候选记忆（记住直通/闲聊入队）", "inputSchema": {
        "type": "object", "properties": {
            "text": {"type": "string", "description": "对话文本"},
            "user_id": {"type": "string", "default": "default"},
            "source": {"type": "string", "default": "dialogue"},
        }, "required": ["text"]}},
    "candidates_pending": {"description": "列出待审候选（被闸门拒绝、等人工裁决）", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}},
    "candidate_review": {"description": "审核候选：approve 进提案链并应用，reject 归档", "inputSchema": {
        "type": "object", "properties": {
            "candidate_id": {"type": "string"},
            "approve": {"type": "boolean"},
            "reason": {"type": "string", "default": ""},
        }, "required": ["candidate_id", "approve"]}},
}

TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "feedback": handle_feedback,
    "backfill": handle_backfill,
    "candidates_pending": handle_candidates_pending,
    "candidate_review": handle_candidate_review,
    "add_dialogue": handle_add_dialogue,
}


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "remembrance", "version": "0.3.1"}}}
    if method == "notifications/initialized":
        return None  # 通知无响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "tools": [{"name": n, **meta} for n, meta in TOOLS.items()]}}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        if not isinstance(params, dict) or not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "params/arguments must be objects"}}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            result = TOOL_HANDLERS[name](args)
        except (ValueError, ValidationError) as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": str(e)}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > 1_000_000:
            continue  # 超长行丢弃，防内存耗尽
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
