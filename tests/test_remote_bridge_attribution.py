"""Standalone bridge contracts exercised over real loopback HTTP, without conf files."""

import copy
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "lantai_mcp_bridge.py"
META_KEY = "io.lantai/attribution"


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location("attribution_bridge_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def loopback(bridge):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), body))
            error = body.get("content") == "http-error"
            response = (
                {"detail": body, "key": self.headers.get("X-API-Key")} if error else {"ok": True}
            )
            data = json.dumps(response).encode()
            self.send_response(422 if error else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # In-memory synthetic key only. _load_conf never opens any file.
    bridge._conf = {
        "host": "127.0.0.1",
        "port": server.server_port,
        "api_key": "synthetic-test-key",
    }
    try:
        yield received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def call(bridge, tool, args, meta=None):
    params = {"name": tool, "arguments": args}
    if meta is not None:
        params["_meta"] = meta
    return bridge.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params})


@pytest.mark.parametrize(
    "tool,path,args",
    [
        ("search", "/search", {"query": "remember my preferences", "domain": "session"}),
        (
            "add",
            "/add",
            {"title": "note", "content": "remember this note", "session_id": "import-A"},
        ),
        ("raw_add", "/add/raw", {"content": "original text", "session_id": "import-A"}),
        ("add_dialogue", "/dialogue", {"text": "hello", "session_id": "import-A", "turn": 4}),
        (
            "dialogue_add_async",
            "/dialogue/async",
            {"text": "hello", "session_id": "import-A", "turn": 5, "idempotency_key": "idem-A"},
        ),
    ],
)
def test_metadata_transfers_only_body_and_preserves_origin(bridge, loopback, tool, path, args):
    args["attribution"] = {"request_session_id": "model-label"}
    before = copy.deepcopy(args)
    context = {"version": 1, "request_session_id": "host-B", "request_id": "call-7"}
    result = call(bridge, tool, args, {META_KEY: context, "other": {"secret": "ignored"}})
    assert "result" in result, result
    actual_path, headers, body = loopback[-1]
    assert actual_path == path
    assert body["attribution"] == context
    assert body.get("session_id") == args.get("session_id")
    assert body.get("turn") == args.get("turn")
    assert body.get("idempotency_key") == args.get("idempotency_key")
    assert all(name.lower() != "x-session-id" for name in headers)
    assert args == before


def test_legacy_call_and_unrelated_tool_receive_no_context(bridge, loopback):
    assert "result" in call(bridge, "search", {"query": "old client"})
    assert "attribution" not in loopback[-1][2]
    assert "result" in call(
        bridge, "feedback", {"memory_id": "memory-A"}, {META_KEY: {"request_session_id": "host-A"}}
    )
    assert loopback[-1][2] == {"memory_id": "memory-A"}


@pytest.mark.parametrize(
    "meta",
    [
        "private-body",
        [],
        {META_KEY: {"version": True}},
        {META_KEY: {"version": 2}},
        {META_KEY: {"request_id": "private-body\n"}},
        {META_KEY: {"request_id": "x" * 201}},
        {META_KEY: {"request_session_id": "x" * 129}},
        {META_KEY: {"user_id": "private-body"}},
        {META_KEY: {"request_id": 7}},
        {META_KEY: []},
    ],
)
def test_bad_metadata_is_safe_invalid_params_without_http(bridge, loopback, meta):
    result = call(bridge, "search", {"query": "private-body"}, meta)
    assert result["error"]["code"] == -32602
    assert "private-body" not in json.dumps(result)
    assert loopback == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"version": 1},
        {"request_id": "  call  ", "request_session_id": "   "},
        {"version": True},
        {"version": 1.0},
        {"request_session_id": "x\u0085"},
        {"request_id": False},
        {"request_id": ["x"]},
        {"request_session_id": " x " * 50},
        {"unknown": "x"},
        [],
    ],
)
def test_standalone_validator_matches_core(bridge, value):
    from lantai.core.attribution import attribution_dict

    try:
        expected = attribution_dict(value)
    except (ValueError, TypeError):
        with pytest.raises(ValueError):
            bridge.attribution_dict(value)
    else:
        assert bridge.attribution_dict(value) == expected


def test_http_errors_never_echo_server_body_or_key(bridge, loopback):
    result = call(bridge, "raw_add", {"content": "http-error", "title": "private-body"})
    assert result["result"].get("isError") is True
    text = json.dumps(result)
    assert "HTTP 422" in text
    assert "private-body" not in text
    assert "synthetic-test-key" not in text


def test_successful_http_is_not_a_tool_failure(bridge, loopback):
    result = call(bridge, "raw_add", {"content": "successful synthetic input"})
    assert result["result"].get("isError", False) is False
    assert json.loads(result["result"]["content"][0]["text"]) == {"ok": True}


def test_direct_handler_validates_attribution(bridge, loopback):
    with pytest.raises(ValueError):
        bridge.handle_search({"query": "private-body", "attribution": {"user_id": "private-body"}})
    assert loopback == []


def test_origin_schema_and_import_do_not_read_conf(bridge):
    assert bridge._conf is None
    for tool in ("add", "raw_add", "add_dialogue", "dialogue_add_async"):
        props = bridge.TOOLS[tool]["inputSchema"]["properties"]
        assert props["session_id"]["type"] == "string"
    for tool in ("add_dialogue", "dialogue_add_async"):
        assert bridge.TOOLS[tool]["inputSchema"]["properties"]["turn"]["type"] == "integer"


@pytest.mark.parametrize("params", [None, [], "private-body"])
def test_non_object_params_returns_invalid_params(bridge, params):
    result = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    assert result["error"]["code"] == -32602


@pytest.mark.parametrize("method", ["notifications/cancelled", "notifications/progress"])
def test_stdio_notifications_never_produce_response_frames(bridge, method):
    assert bridge.handle({"jsonrpc": "2.0", "method": method, "params": {}}) is None
    request = bridge.handle({"jsonrpc": "2.0", "id": 9, "method": method, "params": {}})
    assert request["id"] == 9
    assert request["error"]["code"] == -32601
