"""lantai_mcp_bridge — 远程兰台记忆 MCP 桥（stdio JSON-RPC 2.0）。

协议外壳与原 mcp_server.py 一致（initialize / tools/list / tools/call），
所有工具 handler 改为 HTTP 调远程 REST API（X-API-Key 鉴权）。
零第三方依赖（urllib）。异常 → MCP error，不崩。
"""

import json
import os
import sys
import urllib.error
import urllib.request

# This copy is standalone: do not import lantai or load configuration at import.
ATTRIBUTION_META_KEY = "io.lantai/attribution"
_ATTRIBUTION_TOOLS = frozenset({"add", "raw_add", "search", "add_dialogue", "dialogue_add_async"})


def attribution_dict(value):
    """JSON wire equivalent of lantai.core.attribution, using only stdlib."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"version", "request_session_id", "request_id"}:
        raise ValueError("invalid attribution")
    version = value.get("version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("invalid attribution")
    result = {"version": 1}
    for key, limit in (("request_session_id", 128), ("request_id", 200)):
        identifier = value.get(key)
        if identifier is None:
            continue
        if not isinstance(identifier, str) or len(identifier) > limit:
            raise ValueError("invalid attribution")
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in identifier):
            raise ValueError("invalid attribution")
        identifier = identifier.strip()
        if identifier:
            result[key] = identifier
    return result


def parse_mcp_attribution(meta):
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("invalid attribution metadata")
    return attribution_dict(meta.get(ATTRIBUTION_META_KEY))


def _attributed_payload(args, payload):
    context = attribution_dict(args.get("attribution"))
    if context is not None:
        payload["attribution"] = context
    return payload


PROTOCOL_VERSION = "2024-11-05"
_CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lantai_remote.conf")
_TIMEOUT = 90

TOOLS = {
    "search": {
        "description": "检索相关记忆（远程）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
                "memory_types": {"type": "array", "items": {"type": "string"}},
                "lanes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    "add": {
        "description": "添加记忆（远程）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "minLength": 10, "maxLength": 50000},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "lane": {"type": "string", "default": "general"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "url": {"type": "string", "default": ""},
                "metadata": {"type": "object"},
                "session_id": {"type": "string", "maxLength": 128},
            },
            "required": ["content", "title"],
        },
    },
    "feedback": {
        "description": "反馈记忆有用性",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "query": {"type": "string"},
                "helped": {"type": "boolean"},
                "user_accepted": {"type": "boolean"},
            },
            "required": ["memory_id"],
        },
    },
    "backfill": {
        "description": "回填弱标注",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "used_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["event_id", "used_ids"],
        },
    },
    "add_dialogue": {
        "description": "对话写通道",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "user_id": {"type": "string", "default": "default"},
                "source": {"type": "string", "default": "dialogue"},
                "session_id": {"type": "string", "maxLength": 128},
                "turn": {"type": "integer", "minimum": 0},
            },
            "required": ["text"],
        },
    },
    "dialogue_add_async": {
        "description": "异步对话写通道",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "user_id": {"type": "string", "default": "default"},
                "source": {"type": "string", "default": "dialogue"},
                "session_id": {"type": "string", "maxLength": 128},
                "turn": {"type": "integer", "minimum": 0},
                "idempotency_key": {"type": "string", "maxLength": 200},
            },
            "required": ["text"],
        },
    },
    "candidates_pending": {
        "description": "待审候选",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 50}},
        },
    },
    "candidate_review": {
        "description": "审核候选",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "reason": {"type": "string", "default": ""},
            },
            "required": ["candidate_id", "approve"],
        },
    },
    "raw_add": {
        "description": "原文直存",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "title": {"type": "string", "default": ""},
                "session_id": {"type": "string", "maxLength": 128},
                "lane": {"type": "string", "default": "general"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content"],
        },
    },
    "rollback": {
        "description": "回滚记忆",
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    },
    "conflicts_list": {
        "description": "冲突账本",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "status": {"type": "string", "default": "open"},
            },
        },
    },
    "conflict_resolve": {
        "description": "裁决冲突",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "decision": {"type": "string"},
                "note": {"type": "string", "default": ""},
            },
            "required": ["event_id", "decision"],
        },
    },
    "get_digest": {
        "description": "今日记忆盘点",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "list_memories": {
        "description": "档案浏览：只读分页列出记忆",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lane": {"type": "string"},
                "status": {"type": "string"},
                "decay_class": {"type": "string"},
                "memory_type": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    "get_core_memory": {
        "description": "读取核心记忆块",
        "inputSchema": {"type": "object", "properties": {"namespace": {"type": "string"}}},
    },
    "put_core_memory": {
        "description": "写入核心记忆块",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block": {"type": "string"},
                "content": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["block", "content"],
        },
    },
    "list_edges": {
        "description": "列出某记忆的关联边",
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}, "relation": {"type": "string"}},
            "required": ["memory_id"],
        },
    },
    "create_edge": {
        "description": "创建记忆关联边",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src_id": {"type": "string"},
                "dst_id": {"type": "string"},
                "relation": {"type": "string"},
            },
            "required": ["src_id", "dst_id", "relation"],
        },
    },
    "usage": {
        "description": "最近 7 天每日新增记忆数",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "recall_report": {
        "description": "零召回率监控报告",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 7}},
        },
    },
    "write_checkpoint": {
        "description": "写会话检查点",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "blocks": {"type": "object", "additionalProperties": True},
            },
            "required": ["session_id", "blocks"],
        },
    },
    "latest_checkpoint": {
        "description": "读最近检查点",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "session_id": {"type": "string"},
                "limit": {"type": "integer", "default": 1},
            },
        },
    },
}

_conf = None


def _load_conf():
    global _conf
    if _conf is None:
        with open(_CONF_PATH, encoding="utf-8") as f:
            _conf = json.load(f)
    return _conf


def _http(method, path, payload=None):
    try:
        conf = _load_conf()
        url = f"http://{conf['host']}:{conf['port']}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        # Authentication stays local to this HTTP request, never in metadata.
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-API-Key": conf["api_key"]},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        # Validation bodies may echo user text or credentials. Return status only.
        exc.close()
        return {"error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"error": f"HTTP request failed: {type(exc).__name__}"}


# ---- handlers（原 mcp_server.py 同签名：args dict -> dict）----
def handle_search(args):
    payload = {"query": args.get("query", ""), "top_k": args.get("top_k", 5)}
    if args.get("memory_types"):
        payload["memory_types"] = args["memory_types"]
    if args.get("lanes"):
        payload["lanes"] = args["lanes"]
    return _http("POST", "/search", _attributed_payload(args, payload))


def handle_add(args):
    payload = {
        "content": args.get("content", ""),
        "title": args.get("title", ""),
        "lane": args.get("lane", "general"),
        "tags": args.get("tags", []),
        "url": args.get("url", ""),
        "metadata": args.get("metadata", {}),
    }
    if "session_id" in args:
        payload["session_id"] = args["session_id"]
    return _http("POST", "/add", _attributed_payload(args, payload))


def handle_raw_add(args):
    payload = {
        key: args[key]
        for key in ("content", "title", "lane", "tags", "metadata", "session_id", "shared_scope")
        if key in args
    }
    return _http("POST", "/add/raw", _attributed_payload(args, payload))


def handle_feedback(args):
    return _http("POST", "/feedback", args)


def handle_backfill(args):
    return _http("POST", "/retrieval/backfill", args)


def _dialogue_payload(args):
    payload = {
        key: args[key]
        for key in ("text", "user_id", "source", "session_id", "turn", "idempotency_key")
        if key in args
    }
    return _attributed_payload(args, payload)


def handle_add_dialogue(args):
    return _http("POST", "/dialogue", _dialogue_payload(args))


def handle_dialogue_add_async(args):
    return _http("POST", "/dialogue/async", _dialogue_payload(args))


def handle_candidates_pending(args):
    return _http("GET", f"/candidates/pending?limit={args.get('limit', 50)}")


def handle_candidate_review(args):
    return _http(
        "POST",
        f"/candidates/{args['candidate_id']}/review",
        {"approve": args["approve"], "reason": args.get("reason", "")},
    )


def handle_rollback(args):
    return _http("POST", f"/memory/{args['memory_id']}/rollback", {})


def handle_conflicts_list(args):
    return _http(
        "GET", f"/conflicts?limit={args.get('limit', 50)}&status={args.get('status', 'open')}"
    )


def handle_conflict_resolve(args):
    return _http("POST", f"/conflicts/{args['event_id']}/resolve", args)


def handle_get_digest(args):
    return _http("GET", "/digest/today")


def handle_list_memories(args):
    qs = []
    for k in ("lane", "status", "decay_class", "memory_type"):
        if args.get(k):
            qs.append(f"{k}={urllib.request.quote(str(args[k]))}")
    qs.append(f"limit={args.get('limit', 20)}")
    qs.append(f"offset={args.get('offset', 0)}")
    return _http("GET", "/memories?" + "&".join(qs))


def handle_get_core_memory(args):
    ns = args.get("namespace")
    return _http("GET", "/core-memory" + (f"?namespace={urllib.request.quote(ns)}" if ns else ""))


def handle_put_core_memory(args):
    payload = {"block": args["block"], "content": args["content"]}
    if args.get("namespace"):
        payload["namespace"] = args["namespace"]
    return _http("PUT", "/core-memory", payload)


def handle_list_edges(args):
    mid = urllib.request.quote(args["memory_id"])
    rel = args.get("relation")
    return _http("GET", f"/edges/{mid}" + (f"?relation={urllib.request.quote(rel)}" if rel else ""))


def handle_create_edge(args):
    return _http(
        "POST",
        "/edges",
        {"src_id": args["src_id"], "dst_id": args["dst_id"], "relation": args["relation"]},
    )


def handle_usage(args):
    return _http("GET", "/usage")


def handle_recall_report(args):
    return _http("GET", f"/retrieval/recall-report?days={args.get('days', 7)}")


def handle_write_checkpoint(args):
    return _http(
        "POST", "/checkpoint", {"session_id": args["session_id"], "blocks": args.get("blocks", {})}
    )


def handle_latest_checkpoint(args):
    qs = []
    if args.get("memory_id"):
        qs.append(f"memory_id={urllib.request.quote(args['memory_id'])}")
    if args.get("session_id"):
        qs.append(f"session_id={urllib.request.quote(args['session_id'])}")
    qs.append(f"limit={args.get('limit', 1)}")
    return _http("GET", "/checkpoint/latest?" + "&".join(qs))


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
    "dialogue_add_async": handle_dialogue_add_async,
    "list_memories": handle_list_memories,
    "get_core_memory": handle_get_core_memory,
    "put_core_memory": handle_put_core_memory,
    "list_edges": handle_list_edges,
    "create_edge": handle_create_edge,
    "usage": handle_usage,
    "recall_report": handle_recall_report,
    "write_checkpoint": handle_write_checkpoint,
    "latest_checkpoint": handle_latest_checkpoint,
}


def handle(msg):
    if not isinstance(msg, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "frame must be an object"},
        }
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lantai-remote", "version": "0.4.0"},
            },
        }
    if method in ("notifications/initialized",):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"tools": [{"name": n, **meta} for n, meta in TOOLS.items()]},
        }
    if method == "tools/call":
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        name = params.get("name", "")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        if not isinstance(name, str) or name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": "unknown tool"},
            }
        try:
            if name in _ATTRIBUTION_TOOLS:
                meta = params.get("_meta")
                context = parse_mcp_attribution(meta)
                args = dict(args)
                if isinstance(meta, dict) and ATTRIBUTION_META_KEY in meta:
                    args["attribution"] = context
                elif "attribution" in args:
                    args["attribution"] = attribution_dict(args["attribution"])
            result = TOOL_HANDLERS[name](args)
        except (ValueError, KeyError, TypeError):
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": "invalid tool parameters or attribution"},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main():
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > 1_000_000:
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
