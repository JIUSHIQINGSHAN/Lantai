"""Remote Lantai MCP server over UTF-8 stdio (no local DB or dotenv imports)."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

PROTOCOL_VERSION = "2024-11-05"
_MAX_FRAME = 1_000_000
_MAX_RESPONSE = 4 * 1024 * 1024
_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
_SESSION_PATTERN = r"^[A-Za-z0-9_.-]{3,128}$"


def string(maximum=500, minimum=0, **extra):
    return {"type": "string", "minLength": minimum, "maxLength": maximum, **extra}


def integer(minimum, maximum, default=None):
    schema = {"type": "integer", "minimum": minimum, "maximum": maximum}
    if default is not None:
        schema["default"] = default
    return schema


_ID = string(128, 1, pattern=_ID_PATTERN)
_SESSION = string(128, 3, pattern=_SESSION_PATTERN)
_BOOL = {"type": "boolean"}
_STRINGS = {"type": "array", "maxItems": 500, "items": string(500, 1)}
_OBJECT = {"type": "object"}


@dataclass(frozen=True)
class Tool:
    description: str
    method: str
    route: str
    fields: dict = field(default_factory=dict)
    required: tuple = ()
    rename: dict = field(default_factory=dict)

    def schema(self, name):
        schema = {"type": "object", "properties": self.fields, "additionalProperties": False}
        if self.required:
            schema["required"] = list(self.required)
        return {"name": name, "description": self.description, "inputSchema": schema}


TOOLS = {
    "search": Tool(
        "检索相关记忆（远程）",
        "POST",
        "/search",
        {
            "query": string(8000, 1),
            "top_k": integer(1, 100, 5),
            "memory_types": _STRINGS,
            "lanes": _STRINGS,
            "session_id": _SESSION,
        },
        ("query",),
    ),
    "add": Tool(
        "添加记忆（远程）",
        "POST",
        "/add",
        {
            "content": string(50_000, 10),
            "title": string(500, 1),
            "lane": string(default="general"),
            "tags": _STRINGS,
            "url": string(2048),
            "metadata": _OBJECT,
        },
        ("content", "title"),
    ),
    "feedback": Tool(
        "反馈记忆有用性",
        "POST",
        "/feedback",
        {
            "memory_id": _ID,
            "query": string(8000),
            "helped": _BOOL,
            "user_accepted": _BOOL,
        },
        ("memory_id",),
    ),
    "backfill": Tool(
        "回填弱标注",
        "POST",
        "/retrieval/backfill",
        {
            "event_id": _ID,
            "used_ids": {**_STRINGS, "minItems": 1, "items": _ID},
        },
        ("event_id", "used_ids"),
    ),
    "add_dialogue": Tool(
        "写入对话；传幂等键时异步提交，queued 仅表示已接收",
        "POST",
        "/dialogue",
        {
            "text": string(50_000, 1),
            "user_id": string(128, default="default"),
            "source": string(128, default="dialogue"),
            "session_id": _SESSION,
            "turn": integer(1, 2**31 - 1),
            "idempotency_key": string(200, 1),
        },
        ("text",),
    ),
    "candidates_pending": Tool(
        "待审候选",
        "GET",
        "/candidates/pending",
        {
            "limit": integer(1, 200, 50),
        },
    ),
    "candidate_review": Tool(
        "审核候选",
        "POST",
        "/candidates/{candidate_id}/review",
        {
            "candidate_id": _ID,
            "approve": _BOOL,
            "reason": string(5000),
        },
        ("candidate_id", "approve"),
    ),
    "raw_add": Tool(
        "原文直存",
        "POST",
        "/add/raw",
        {
            "content": string(200_000, 1),
            "title": string(),
            "lane": string(default="general"),
            "tags": _STRINGS,
        },
        ("content",),
    ),
    "rollback": Tool(
        "回滚记忆", "POST", "/memory/{memory_id}/rollback", {"memory_id": _ID}, ("memory_id",)
    ),
    "conflicts_list": Tool(
        "冲突账本",
        "GET",
        "/conflicts",
        {
            "limit": integer(1, 500, 50),
            "status": string(default="open"),
        },
    ),
    "conflict_resolve": Tool(
        "裁决冲突",
        "POST",
        "/conflicts/{event_id}/resolve",
        {
            "event_id": _ID,
            "decision": {**string(100, 1), "enum": ["resolved", "dismissed"]},
            "note": string(5000, 1, pattern=r"[\s\S]*\S[\s\S]*"),
        },
        ("event_id", "decision", "note"),
    ),
    "get_digest": Tool("今日记忆盘点", "GET", "/digest/today"),
    "list_memories": Tool(
        "档案浏览：只读分页列出记忆",
        "GET",
        "/memories",
        {
            **dict.fromkeys(("lane", "status", "decay_class", "memory_type"), string()),
            "limit": integer(1, 500, 20),
            "offset": integer(0, 2**31 - 1, 0),
        },
    ),
    "get_core_memory": Tool("读取核心记忆块", "GET", "/core-memory", {"namespace": string(128)}),
    "put_core_memory": Tool(
        "写入核心记忆块",
        "PUT",
        "/core-memory",
        {
            "block": string(128, 1),
            "content": string(50_000, 1),
            "namespace": string(128),
        },
        ("block", "content"),
    ),
    "list_edges": Tool(
        "列出某记忆的关联边",
        "GET",
        "/edges/{memory_id}",
        {
            "memory_id": _ID,
            "relation": string(),
        },
        ("memory_id",),
    ),
    "create_edge": Tool(
        "创建记忆关联边",
        "POST",
        "/edges",
        {
            "src_id": _ID,
            "dst_id": _ID,
            "relation": string(128, 1),
        },
        ("src_id", "dst_id", "relation"),
        {"src_id": "source_memory_id", "dst_id": "target_memory_id"},
    ),
    "usage": Tool("最近 7 天每日新增记忆数", "GET", "/usage"),
    "recall_report": Tool(
        "零召回率监控报告",
        "GET",
        "/retrieval/recall-report",
        {
            "days": integer(1, 365, 7),
        },
    ),
    "write_checkpoint": Tool(
        "写会话检查点",
        "POST",
        "/checkpoint",
        {
            "session_id": _SESSION,
            "blocks": _OBJECT,
        },
        ("session_id", "blocks"),
    ),
    "latest_checkpoint": Tool(
        "读最近/指定会话检查点（对象/null）；兼容 memory_id 历史查询，新增调用请用 list_memory_checkpoints",
        "GET",
        "/checkpoint/latest",
        {
            "memory_id": _ID,
            "session_id": _SESSION,
            "limit": integer(1, 100, 1),
        },
    ),
    "list_memory_checkpoints": Tool(
        "按记忆 ID 列出变更检查点历史，返回 {checkpoints: [...]}；latest_checkpoint 的旧参数仍兼容",
        "GET",
        "/checkpoint",
        {"memory_id": _ID, "limit": integer(1, 100, 20)},
        ("memory_id",),
    ),
}


class UpstreamError(Exception):
    """A stable, redacted diagnostic safe to expose to the MCP caller."""


class InvalidArguments(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    scheme: str
    host: str
    port: int | None
    base_path: str
    auth_header: str
    auth_value: str = field(repr=False)
    timeout: float = 10.0

    @classmethod
    def load(cls, env):
        endpoint_error = (
            "LANTAI_BRIDGE_BASE_URL must be HTTPS (HTTP requires explicit literal-loopback opt-in)"
        )
        base = env.get("LANTAI_BRIDGE_BASE_URL", "")
        try:
            if not base or re.search(r"[\x00-\x20\x7f\\]", base):
                raise ValueError
            url = urlsplit(base)
            host, port = url.hostname, url.port
            if not host or url.username is not None or "?" in base or "#" in base:
                raise ValueError
            if not re.fullmatch(r"(?:/[A-Za-z0-9_.~-]+)*/?", url.path) or {".", ".."}.intersection(
                url.path.split("/")
            ):
                raise ValueError
            if url.scheme == "http" and env.get("LANTAI_BRIDGE_ALLOW_LOOPBACK_HTTP") == "1":
                if not ipaddress.ip_address(host).is_loopback or "%" in host:
                    raise ValueError
            elif url.scheme != "https":
                raise ValueError
        except ValueError:
            raise ValueError(endpoint_error) from None
        bearer = env.get("LANTAI_BRIDGE_BEARER_TOKEN", "")
        api_key = env.get("LANTAI_BRIDGE_API_KEY", "")
        if bool(bearer) == bool(api_key) or not re.fullmatch(
            r"[\x21-\x7e]{1,4096}", bearer or api_key
        ):
            raise ValueError(
                "Set exactly one valid LANTAI_BRIDGE_BEARER_TOKEN or LANTAI_BRIDGE_API_KEY"
            )
        try:
            timeout = float(env.get("LANTAI_BRIDGE_TIMEOUT_S", "10"))
            if not math.isfinite(timeout) or not 0.05 <= timeout <= 55:
                raise ValueError
        except ValueError:
            raise ValueError("LANTAI_BRIDGE_TIMEOUT_S must be in [0.05, 55]") from None
        header, value = ("Authorization", f"Bearer {bearer}") if bearer else ("X-API-Key", api_key)
        return cls(url.scheme, host, port, url.path.rstrip("/"), header, value, timeout)

    def request(self, method, path, payload, session_id=None):
        # http.client connects directly (no ambient proxy or redirect following).
        conn_type = (
            http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        )
        conn = conn_type(self.host, self.port, timeout=self.timeout)
        headers = {self.auth_header: self.auth_value, "Accept": "application/json"}
        if session_id is not None:
            headers["X-Session-Id"] = session_id
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        deadline = time.monotonic() + self.timeout
        try:
            conn.request(method, self.base_path + path, body=data, headers=headers)
            # Keep the socket captured before getresponse: http.client may detach it
            # from the connection for a close-delimited response.
            transport = conn.sock
            if transport is not None:
                transport.settimeout(_remaining(deadline))
            with conn.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise UpstreamError(f"Lantai upstream HTTP {response.status}")
                body = bytearray()
                while len(body) <= _MAX_RESPONSE:
                    remaining = _remaining(deadline)
                    if transport is not None:
                        transport.settimeout(remaining)
                    chunk = response.read1(min(64 * 1024, _MAX_RESPONSE + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                if len(body) > _MAX_RESPONSE:
                    raise UpstreamError("Lantai upstream response too large")
            result = (
                json.loads(body.decode("utf-8"), parse_constant=_reject_constant) if body else {}
            )
            if not isinstance(result, (dict, list)) and result is not None:
                raise ValueError
            if (
                isinstance(result, dict)
                and result.get("error")
                and result.get("status") != "pending"
            ):
                raise UpstreamError("Lantai upstream reported a failed operation")
            return result
        except TimeoutError:
            raise UpstreamError("Lantai upstream timeout; write outcome may be unknown") from None
        except (ValueError, RecursionError):
            raise UpstreamError("Lantai upstream invalid JSON response") from None
        except (OSError, http.client.HTTPException):
            raise UpstreamError(
                "Lantai upstream connection failed; write outcome may be unknown"
            ) from None
        finally:
            conn.close()


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    return left


def _reject_constant(_):
    raise ValueError("Non-finite JSON constant")


def _valid(value, schema):
    match schema["type"]:
        case "string":
            return (
                isinstance(value, str)
                and schema.get("minLength", 0) <= len(value) <= schema["maxLength"]
                and ("pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None)
                and ("enum" not in schema or value in schema["enum"])
            )
        case "integer":
            return type(value) is int and schema["minimum"] <= value <= schema["maximum"]
        case "boolean":
            return type(value) is bool
        case "array":
            return (
                isinstance(value, list)
                and schema.get("minItems", 0) <= len(value) <= schema["maxItems"]
                and all(_valid(elem, schema["items"]) for elem in value)
            )
        case "object":
            return isinstance(value, dict)
    return False


def build_request(name, arguments):
    tool = TOOLS[name]
    if not isinstance(arguments, dict) or arguments.keys() - tool.fields.keys():
        raise InvalidArguments("Invalid tool arguments")
    for key in tool.required:
        if key not in arguments:
            raise InvalidArguments(f"Missing argument: {key}")
    for key, value in arguments.items():
        if not _valid(value, tool.fields[key]):
            raise InvalidArguments(f"Invalid argument: {key}")
    fields = {k: v["default"] for k, v in tool.fields.items() if "default" in v}
    fields.update(arguments)
    path = tool.route
    for key in re.findall(r"\{(\w+)\}", path):
        value = fields.pop(key)
        # Only validated URL-unreserved identifiers reach path parameters.
        if value in {".", ".."}:
            raise InvalidArguments(f"Invalid argument: {key}")
        path = path.replace("{" + key + "}", value)
    session_id = None
    if name == "search":
        session_id = fields.pop("session_id", None)
    elif name == "add_dialogue":
        if "turn" in fields and "session_id" not in fields:
            raise InvalidArguments("turn requires session_id")
        if "idempotency_key" in fields:
            if not fields["idempotency_key"].strip():
                raise InvalidArguments("Invalid argument: idempotency_key")
            path = "/dialogue/async"
        session_id = fields.get("session_id")
    elif name == "latest_checkpoint":
        if "session_id" in fields and "memory_id" in fields:
            raise InvalidArguments("Pass session_id or memory_id, not both")
        if "session_id" in fields:
            path = "/checkpoint"
            fields.pop("limit", None)
        elif "memory_id" in fields:
            path = "/checkpoint"
        else:
            fields.clear()
    fields = {tool.rename.get(k, k): v for k, v in fields.items()}
    if tool.method == "GET":
        return tool.method, path + ("?" + urlencode(fields) if fields else ""), None, session_id
    return tool.method, path, fields, session_id


def handle(message, config):
    if not isinstance(message, dict) or "id" not in message:
        return None
    request_id = message["id"]
    if type(request_id) not in (str, int) and request_id is not None:
        request_id = None
    envelope = {"jsonrpc": "2.0", "id": request_id}

    def failure(code, detail):
        return {**envelope, "error": {"code": code, "message": detail}}

    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return failure(-32600, "Invalid request")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return failure(-32602, "Invalid params")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lantai-remote", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [tool.schema(name) for name, tool in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS:
            return failure(-32602, "Unknown tool")
        try:
            request = build_request(name, params.get("arguments", {}))
            value = config.request(*request)
            result = {"content": [{"type": "text", "text": _json(value)}]}
        except InvalidArguments as exc:
            return failure(-32602, str(exc))
        except UpstreamError as exc:
            result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except (ValueError, RecursionError):
            return failure(-32602, "Invalid JSON tool arguments or result")
    else:
        return failure(-32601, "Method not found")
    return {**envelope, "result": result}


def _json(value):
    # Escape isolated surrogate code units without breaking normal UTF-8 text.
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def main():
    try:
        config = Config.load(os.environ)
    except ValueError as exc:
        print(f"lantai-mcp-bridge: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    stream = sys.stdin.buffer
    while line := stream.readline(_MAX_FRAME + 1):
        if len(line) > _MAX_FRAME:
            while line and not line.endswith(b"\n"):
                line = stream.readline(_MAX_FRAME + 1)
            sys.stdout.buffer.write(
                b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Oversize frame"}}\n'
            )
            sys.stdout.buffer.flush()
            continue
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            continue
        reply = handle(message, config)
        if reply is not None:
            sys.stdout.buffer.write((_json(reply) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
