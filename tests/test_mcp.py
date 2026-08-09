"""MCP 协议测试：标准错误码 + 输入校验 + 异常隔离"""
import importlib.util
import json
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
    names = [t["name"] for t in resp["result"]["tools"]]
    assert len(resp["result"]["tools"]) == 7  # .../candidates_pending/candidate_review/add_dialogue
    assert "candidates_pending" in names
    assert "candidate_review" in names
    assert "add_dialogue" in names


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


def test_backfill_ok():
    """backfill 工具：合法输入 → 调用 backfill_used_ids + 返回 ok。"""
    mod = _load_mcp()
    with patch("remembrance.observability.retrieval_log.backfill_used_ids") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                           "params": {"name": "backfill",
                                      "arguments": {"event_id": "ev_1",
                                                    "used_ids": ["mem_1", "mem_2"]}}})
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["event_id"] == "ev_1"
    assert payload["used_count"] == 2
    m.assert_called_once_with("ev_1", ["mem_1", "mem_2"])


def test_backfill_validation():
    """backfill 工具：非法输入 → -32602，不调底层。"""
    mod = _load_mcp()
    with patch("remembrance.observability.retrieval_log.backfill_used_ids") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                           "params": {"name": "backfill",
                                      "arguments": {"event_id": "", "used_ids": []}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_candidates_pending_ok():
    """candidates_pending 工具：合法输入 → 调用 service + 返回列表。"""
    mod = _load_mcp()
    with patch("remembrance.services.candidate_service.list_pending_candidates",
               return_value={"candidates": []}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": "candidates_pending",
                                      "arguments": {"limit": 10}}})
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    assert json.loads(text) == {"candidates": []}
    m.assert_called_once_with(10)


def test_candidate_review_ok():
    """candidate_review 工具：approve=false → 归档。"""
    mod = _load_mcp()
    with patch("remembrance.services.candidate_service.review_candidate",
               return_value={"ok": True, "candidate_status": "rejected"}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                           "params": {"name": "candidate_review",
                                      "arguments": {"candidate_id": "cand_1",
                                                    "approve": False,
                                                    "reason": "不相关"}}})
    assert "error" not in resp
    m.assert_called_once_with("cand_1", approve=False, reason="不相关")


def test_candidate_review_validation():
    """candidate_review 工具：非法输入 → -32602，不调底层。"""
    mod = _load_mcp()
    with patch("remembrance.services.candidate_service.review_candidate") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                           "params": {"name": "candidate_review",
                                      "arguments": {"candidate_id": "", "approve": "yes"}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_add_dialogue_ok():
    """add_dialogue 工具：合法输入 → 调用 ingest_dialogue + 返回结果。"""
    mod = _load_mcp()
    with patch("remembrance.ingestion.dialogue.ingest_dialogue",
               return_value={"ingested": True, "candidate_id": "cand_1",
                             "fastpath": True, "lane": "general",
                             "status": "fastpath"}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                           "params": {"name": "add_dialogue",
                                      "arguments": {"text": "记住：明天开会"}}})
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    assert json.loads(text)["ingested"] is True
    m.assert_called_once_with("记住：明天开会", user_id="default", source="dialogue")


def test_add_dialogue_validation():
    """add_dialogue 工具：空文本 → -32602，不调底层。"""
    mod = _load_mcp()
    with patch("remembrance.ingestion.dialogue.ingest_dialogue") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                           "params": {"name": "add_dialogue",
                                      "arguments": {"text": "   "}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_search_response_has_evidence():
    """search 响应含来源说明（evidence），event_id 透出不受影响。"""
    mod = _load_mcp()
    # hybrid_search / relevance_check 在 mcp_server 模块顶部已绑定，patch 模块属性
    with patch.object(mod, "hybrid_search",
                      return_value=[{"score": 0.9,
                                     "memory": {"id": "mem_1",
                                                "content": "Python 资料"}}]), \
         patch.object(mod, "relevance_check",
                      return_value={"needs_memory": True, "reason": "t",
                                    "scope": "t"}), \
         patch("remembrance.observability.retrieval_log.log_retrieval",
               return_value="ev_1"):
        resp = mod.handle({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                           "params": {"name": "search",
                                      "arguments": {"query": "python", "top_k": 5}}})
    assert "error" not in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["evidence"][0]["id"] == "mem_1"
    assert payload["event_id"] == "ev_1"
