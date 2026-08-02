"""MCP 协议测试：标准错误码 + 输入校验 + 异常隔离"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

MCP_PATH = Path(__file__).parent.parent / "scripts" / "mcp_server.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_initialize():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "remembrance"
    assert resp["result"]["protocolVersion"] == mod.PROTOCOL_VERSION


def test_tools_list():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(resp["result"]["tools"]) == 3


def test_unknown_tool():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "nope", "arguments": {}}})
    assert resp["error"]["code"] == -32602


def test_top_k_out_of_range():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "search", "arguments": {"query": "测试", "top_k": 999}}})
    assert resp["error"]["code"] == -32602


def test_handler_exception_is_isolated():
    mod = _load_mcp()
    with patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}), \
         patch.object(mod, "TOOL_HANDLERS",
                      {"boom": lambda p: (_ for _ in ()).throw(RuntimeError("x"))}):
        resp = mod.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                           "params": {"name": "boom", "arguments": {}}})
    assert resp["error"]["code"] == -32603
    assert "RuntimeError" in resp["error"]["message"]


def test_non_object_args():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "search", "arguments": "not-a-dict"}})
    assert resp["error"]["code"] == -32602
