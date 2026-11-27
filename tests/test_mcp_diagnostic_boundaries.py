"""阶段6-B：真实解析/分发路径的异常边界，不替换内部逻辑。"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

MCP_PATH = Path(__file__).resolve().parents[1] / "lantai" / "cli" / "mcp.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_diagnostic_boundaries", MCP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("method", [None, True, 42, [], {}])
def test_invalid_method_is_request_error(method):
    module = _load_mcp()
    reply = module.handle({"jsonrpc": "2.0", "id": 5, "method": method})
    assert reply["id"] == 5
    assert reply["error"]["code"] == -32600
    assert "method" in reply["error"]["message"]


@pytest.mark.parametrize(
    "bad_line",
    [
        # 非法尾缀保证大栈平台也验证坏 JSON，而非合法的非对象帧。
        "[" * 100_000 + "0" + "]" * 100_000 + " invalid",
        '{"id":' + "9" * 5000 + ',"method":"ping"}',
        '{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
        '{"jsonrpc":"2.0","id":Infinity,"method":"ping"}',
        '{"jsonrpc":"2.0","id":-Infinity,"method":"ping"}',
    ],
    ids=["deep-json", "oversized-integer", "nan", "infinity", "negative-infinity"],
)
def test_bad_json_is_silent_and_next_ping_survives(monkeypatch, bad_line):
    module = _load_mcp()
    incoming = bad_line + '\n{"jsonrpc":"2.0","id":23,"method":"ping"}\n'
    outgoing = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(incoming))
    monkeypatch.setattr(sys, "stdout", outgoing)
    module.main()
    replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert replies == [{"jsonrpc": "2.0", "id": 23, "result": {}}]


def test_invalid_method_frame_does_not_become_internal_error(monkeypatch):
    module = _load_mcp()
    outgoing = io.StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"id":6,"method":null}\n{"id":7,"method":"ping"}\n'),
    )
    monkeypatch.setattr(sys, "stdout", outgoing)
    module.main()
    invalid, alive = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert invalid["error"]["code"] == -32600
    assert alive == {"jsonrpc": "2.0", "id": 7, "result": {}}
