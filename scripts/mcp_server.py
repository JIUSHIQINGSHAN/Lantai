"""MCP Server——标准协议写操作（search/add/feedback）

与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）
"""
import json
import sys

from remembrance.models.schemas import AddMemoryReq, SearchReq, FeedbackReq
from remembrance.services.memory_service import add_memory
from remembrance.services.evolution_service import record_feedback_entry
from remembrance.retrieval.hybrid import hybrid_search
from remembrance.gate.prefilter import relevance_check


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
    "search": {"description": "Search memories", "handler": handle_search},
    "add": {"description": "Add a memory", "handler": handle_add},
    "feedback": {"description": "Record feedback", "handler": handle_feedback},
}


def main():
    """简易 MCP 协议处理循环（stdin/stdout JSON-RPC）。"""
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        params = msg.get("params", {})
        msg_id = msg.get("id")

        if method == "tools/list":
            result = {
                "tools": [
                    {"name": name, "description": t["description"]}
                    for name, t in TOOLS.items()
                ]
            }
        elif method in TOOLS:
            try:
                result = TOOLS[method]["handler"](params)
            except Exception as e:
                result = {"error": str(e)}
        else:
            result = {"error": f"unknown method: {method}"}

        response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        print(json.dumps(response, ensure_ascii=False))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
