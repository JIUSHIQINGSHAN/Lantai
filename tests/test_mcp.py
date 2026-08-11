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
    assert resp["result"]["serverInfo"]["name"] == "lantai"
    assert resp["result"]["protocolVersion"] == mod.PROTOCOL_VERSION


def test_tools_list():
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert len(resp["result"]["tools"]) == 12  # + raw_add/rollback/conflicts_list/conflict_resolve
    assert "candidates_pending" in names
    assert "candidate_review" in names
    assert "add_dialogue" in names
    assert "get_digest" in names
    assert "raw_add" in names
    assert "rollback" in names
    assert "conflicts_list" in names
    assert "conflict_resolve" in names


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
    with patch("lantai.observability.retrieval_log.backfill_used_ids") as m:
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
    with patch("lantai.observability.retrieval_log.backfill_used_ids") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                           "params": {"name": "backfill",
                                      "arguments": {"event_id": "", "used_ids": []}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_candidates_pending_ok():
    """candidates_pending 工具：合法输入 → 调用 service + 返回列表。"""
    mod = _load_mcp()
    with patch("lantai.services.candidate_service.list_pending_candidates",
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
    with patch("lantai.services.candidate_service.review_candidate",
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
    with patch("lantai.services.candidate_service.review_candidate") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                           "params": {"name": "candidate_review",
                                      "arguments": {"candidate_id": "", "approve": "yes"}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_add_dialogue_ok():
    """add_dialogue 工具：合法输入 → 调用 ingest_dialogue + 返回结果。"""
    mod = _load_mcp()
    with patch("lantai.ingestion.dialogue.ingest_dialogue",
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
    with patch("lantai.ingestion.dialogue.ingest_dialogue") as m:
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
         patch("lantai.observability.retrieval_log.log_retrieval",
               return_value="ev_1"):
        resp = mod.handle({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                           "params": {"name": "search",
                                      "arguments": {"query": "python", "top_k": 5}}})
    assert "error" not in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["evidence"][0]["id"] == "mem_1"
    assert payload["event_id"] == "ev_1"


def test_raw_add_ok():
    """raw_add 工具：合法输入 → 调用 add_raw_memory + 返回结果。"""
    mod = _load_mcp()
    with patch("lantai.services.memory_service.add_raw_memory",
               return_value={"memory_id": "mem_1", "dedup": False,
                             "verbatim": True}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                           "params": {"name": "raw_add",
                                      "arguments": {"content": "docker run -p 8080:80",
                                                    "lane": "fact"}}})
    assert "error" not in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["memory_id"] == "mem_1"
    m.assert_called_once()


def test_raw_add_validation():
    """raw_add 工具：空内容 → -32602，不调底层。"""
    mod = _load_mcp()
    with patch("lantai.services.memory_service.add_raw_memory") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 21, "method": "tools/call",
                           "params": {"name": "raw_add",
                                      "arguments": {"content": "  "}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_rollback_ok():
    """rollback 工具：合法输入 → 调用 promoter.rollback。"""
    mod = _load_mcp()
    with patch("lantai.evolution.promoter.rollback",
               return_value={"ok": True}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 22, "method": "tools/call",
                           "params": {"name": "rollback",
                                      "arguments": {"memory_id": "mem_1"}}})
    assert "error" not in resp
    m.assert_called_once_with("mem_1")


def test_rollback_validation():
    """rollback 工具：空 id → -32602。"""
    mod = _load_mcp()
    with patch("lantai.evolution.promoter.rollback") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 23, "method": "tools/call",
                           "params": {"name": "rollback",
                                      "arguments": {"memory_id": ""}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_conflicts_list_ok():
    """conflicts_list 工具：合法输入 → 调用 service + 返回列表。"""
    mod = _load_mcp()
    with patch("lantai.services.conflict_service.list_conflict_events",
               return_value={"events": []}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 24, "method": "tools/call",
                           "params": {"name": "conflicts_list",
                                      "arguments": {"limit": 10, "status": "open"}}})
    assert "error" not in resp
    m.assert_called_once_with(10, "open")


def test_conflicts_list_validation():
    """conflicts_list 工具：非法 status → -32602。"""
    mod = _load_mcp()
    with patch("lantai.services.conflict_service.list_conflict_events") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 25, "method": "tools/call",
                           "params": {"name": "conflicts_list",
                                      "arguments": {"status": "nope"}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()


def test_conflict_resolve_ok():
    """conflict_resolve 工具：合法输入 → 调用 service。"""
    mod = _load_mcp()
    with patch("lantai.services.conflict_service.resolve_conflict_event",
               return_value={"ok": True, "event_id": "cfev_1",
                             "status": "resolved"}) as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 26, "method": "tools/call",
                           "params": {"name": "conflict_resolve",
                                      "arguments": {"event_id": "cfev_1",
                                                    "decision": "resolved"}}})
    assert "error" not in resp
    m.assert_called_once_with("cfev_1", "resolved", "")


def test_conflict_resolve_validation():
    """conflict_resolve 工具：非法 decision → -32602。"""
    mod = _load_mcp()
    with patch("lantai.services.conflict_service.resolve_conflict_event") as m:
        resp = mod.handle({"jsonrpc": "2.0", "id": 27, "method": "tools/call",
                           "params": {"name": "conflict_resolve",
                                      "arguments": {"event_id": "cfev_1",
                                                    "decision": "maybe"}}})
    assert resp["error"]["code"] == -32602
    m.assert_not_called()
