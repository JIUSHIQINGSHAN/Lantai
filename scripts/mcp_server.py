"""MCP Server——标准协议写操作（search/add/feedback）

与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）
标准 MCP JSON-RPC 2.0 协议
"""
import json
import sys

from remembrance.models.schemas import AddMemoryReq, SearchReq, FeedbackReq
from remembrance.services.memory_service import add_memory
from remembrance.services.evolution_service import record_feedback_entry
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check

PROTOCOL_VERSION = "2024-11-05"


def handle_search(params: dict) -> dict:
    query = params.get("query", "")
    top_k = params.get("top_k", 5)
    gate = relevance_check(query)
    if not gate["needs_memory"]:
        return {"results": [], "gate": gate}
    results = hybrid_search(query, top_k=top_k)
    return {"results": results, "gate": gate}


def handle_add(params: dict) -> dict:
    req = AddMemoryReq(
        title=params.get("title", ""),
        content=params.get("content", ""),
        lane=params.get("lane", "general"),
    )
    return add_memory(req)


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
}

TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "feedback": handle_feedback,
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
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        result = TOOL_HANDLERS[name](args)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
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
