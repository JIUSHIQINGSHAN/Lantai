"""MCP Server——标准协议写操作（search/add/feedback）

与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）
标准 MCP JSON-RPC 2.0 协议
"""
import json
import os
import sys

# 使子进程无论 cwd 在哪都能 import lantai（Hermes 拉 MCP 时 cwd 不可控）
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

from lantai.models.schemas import AddMemoryReq, SearchReq, FeedbackReq
from lantai.services.memory_service import add_memory
from lantai.services.evolution_service import record_feedback_entry
from lantai.retrieval.hybrid import hybrid_search
from lantai.gate.prefilter import relevance_check

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
    # Ticket 04: 检索透明——命中来源说明（id + 摘要 + 分数）
    from lantai.retrieval.evidence import build_evidence
    return {"results": results, "gate": gate, "event_id": event_id,
            "evidence": build_evidence(results)}


def _try_log(query: str, results: list, latency_ms: int, gate: dict) -> str | None:
    """检索事件埋点（方向二）：失败零侵入。返回 event_id 供生成侧回填 used_ids。"""
    try:
        from lantai.observability.retrieval_log import log_retrieval
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
    from lantai.observability.retrieval_log import backfill_used_ids as _bf
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
    from lantai.ingestion.dialogue import ingest_dialogue
    return ingest_dialogue(text, user_id=user_id, source=source)


def handle_candidates_pending(params: dict) -> dict:
    """待审候选列表（Ticket 02）——被闸门拒绝的候选进此队列等人工裁决。"""
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from lantai.services.candidate_service import list_pending_candidates
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
    from lantai.services.candidate_service import review_candidate
    return review_candidate(candidate_id, approve=approve, reason=reason)


def handle_get_digest(params: dict) -> dict:
    """当日记忆盘点报告（Ticket 03）。"""
    from lantai.workers.digest_worker import load_today_digest
    return load_today_digest()



def handle_feedback(params: dict) -> dict:
    req = FeedbackReq(
        memory_id=params.get("memory_id", ""),
        query=params.get("query", ""),
        helped=params.get("helped", False),
        user_accepted=params.get("user_accepted", False),
    )
    return record_feedback_entry(req)



def handle_raw_add(params: dict) -> dict:
    """原文直存（verbatim）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""
    from lantai.models.schemas import RawMemoryReq
    from lantai.services.memory_service import add_raw_memory
    content = params.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    req = RawMemoryReq(
        title=params.get("title", "") or "",
        content=content,
        lane=params.get("lane", "general"),
        tags=params.get("tags", []) or [],
    )
    return add_raw_memory(req)


def handle_rollback(params: dict) -> dict:
    """回滚记忆到上一版本（Checkpoint 快照）。"""
    memory_id = params.get("memory_id", "")
    if not isinstance(memory_id, str) or not memory_id:
        raise ValueError("memory_id must be a non-empty string")
    from lantai.evolution.promoter import rollback as _rollback
    return _rollback(memory_id)


def handle_conflicts_list(params: dict) -> dict:
    """列出冲突账本事件（默认 open，等待人工裁决）。"""
    limit = params.get("limit", 50)
    status = params.get("status", "open")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    if not isinstance(status, str) or status not in ("open", "resolved", "dismissed", "all"):
        raise ValueError("status must be open/resolved/dismissed/all")
    from lantai.services.conflict_service import list_conflict_events
    return list_conflict_events(limit, status)


def handle_conflict_resolve(params: dict) -> dict:
    """裁决冲突事件：resolved（确认冲突成立）/ dismissed（误报）。"""
    event_id = params.get("event_id", "")
    decision = params.get("decision", "")
    note = params.get("note", "")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if decision not in ("resolved", "dismissed"):
        raise ValueError("decision must be 'resolved' or 'dismissed'")
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    from lantai.services.conflict_service import resolve_conflict_event
    return resolve_conflict_event(event_id, decision, note)

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
    "raw_add": {"description": "原文直存（verbatim）：内容直入 FTS5+向量，零 LLM", "inputSchema": {
        "type": "object", "properties": {
            "content": {"type": "string", "description": "原文内容（代码/日志/配置等）"},
            "title": {"type": "string", "default": ""},
            "lane": {"type": "string", "default": "general"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["content"]}},
    "rollback": {"description": "回滚记忆到上一版本（Checkpoint 快照）", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string"},
        }, "required": ["memory_id"]}},
    "conflicts_list": {"description": "列出冲突账本事件（确定性规则命中记录）", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
            "status": {"type": "string", "default": "open"},
        }}},
    "conflict_resolve": {"description": "裁决冲突事件：resolved / dismissed", "inputSchema": {
        "type": "object", "properties": {
            "event_id": {"type": "string"},
            "decision": {"type": "string", "description": "resolved | dismissed"},
            "note": {"type": "string", "default": ""},
        }, "required": ["event_id", "decision"]}},
    "get_digest": {"description": "获取今日记忆盘点报告（摘要 + 五项统计）", "inputSchema": {
        "type": "object", "properties": {}}},
}

TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "feedback": handle_feedback,
    "backfill": handle_backfill,
    "candidates_pending": handle_candidates_pending,
    "candidate_review": handle_candidate_review,
    "get_digest": handle_get_digest,
    "raw_add": handle_raw_add,
    "rollback": handle_rollback,
    "conflicts_list": handle_conflicts_list,
    "conflict_resolve": handle_conflict_resolve,
    "add_dialogue": handle_add_dialogue,
}


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lantai", "version": "0.3.1"}}}
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
