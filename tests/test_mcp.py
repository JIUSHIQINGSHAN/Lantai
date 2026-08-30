"""MCP 协议测试：标准错误码 + 输入校验 + 异常隔离"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

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
    assert len(resp["result"]["tools"]) == 48  # 第十一波：札记 scratchpad_get/write (ADR-0032)
    assert "persona_get" in names
    assert "persona_set" in names
    assert "candidate_refine" in names
    assert "kaogong_eval" in names
    assert "scratchpad_get" in names
    assert "scratchpad_write" in names
    assert "candidates_pending" in names
    assert "candidate_review" in names
    assert "add_dialogue" in names
    assert "get_digest" in names
    assert "raw_add" in names
    assert "rollback" in names
    assert "conflicts_list" in names
    assert "conflict_resolve" in names
    assert "obsidian_sync" in names
    assert "mem_help" in names
    assert "mem_sync" in names
    assert "mem_create_skill" in names
    assert "offload_read" in names
    assert "wiki_read" in names
    assert "obsidian_sync" in names
    assert "mem_recent" in names
    assert "mem_stats" in names
    assert "mem_health" in names
    assert "autodream_report" in names
    assert "autodream_trigger" in names
    assert "proposals_list" in names
    assert "proposal_decide" in names
    assert "tree_view" in names
    assert "tree_add" in names
    assert "tree_assign" in names
    assert "crystals_list" in names
    assert "crystals_detect" in names
    assert "crystal_decide" in names
    assert "reflect_run" in names
    assert "mem_usage" in names
    assert "core_memory_get" in names
    assert "verbatim_search" in names
    assert "graph_view" in names


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


def test_tools_metadata_standards():
    """MCP 工具元数据合规：多客户端（Claude Code/Cursor/Gemini CLI）要求
    每个工具都有 name + description + inputSchema，且 required 是 properties 子集。"""
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert len(tools) >= 12
    for t in tools:
        assert t["name"].strip(), f"tool 缺 name: {t}"
        assert t["description"].strip(), f"tool {t['name']} 缺 description"
        schema = t["inputSchema"]
        assert isinstance(schema, dict) and schema.get("type") == "object",             f"tool {t['name']} inputSchema 必须是 object"
        props = schema.get("properties", {})
        assert isinstance(props, dict), f"tool {t['name']} properties 必须是 dict"
        for req in schema.get("required", []):
            assert req in props, f"tool {t['name']} required {req} 不在 properties"


def test_ping_and_initialized_notification():
    """MCP 协议基础：ping 有响应；initialized 通知无响应（多客户端握手兼容）。"""
    mod = _load_mcp()
    assert mod.handle({"jsonrpc": "2.0", "id": 11, "method": "ping"})["result"] == {}
    assert mod.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_call_missing_arguments_rejected():
    """tools/call 缺参（params 非 object / arguments 非 object）→ -32602 标准错误。"""
    mod = _load_mcp()
    r1 = mod.handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                     "params": {"name": "search"}})
    assert r1["error"]["code"] == -32602
    r2 = mod.handle({"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                     "params": {"name": "search", "arguments": "not-a-dict"}})
    assert r2["error"]["code"] == -32602

def test_mem_help_tool_call():
    """mem:help 命令式工具：tools/call 返回命令表。"""
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                       "params": {"name": "mem_help", "arguments": {}}})
    assert "result" in resp
    text = json.loads(resp["result"]["content"][0]["text"])
    assert text["command"] == "mem:help"
    assert "mem_create_skill" in text["text"]


def test_mem_create_skill_validation_error():
    """mem:create-skill 参数校验：缺 name/steps → -32602。"""
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                       "params": {"name": "mem_create_skill",
                                  "arguments": {"name": "x", "steps": []}}})
    assert resp["error"]["code"] == -32602
    resp = mod.handle({"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                       "params": {"name": "mem_create_skill",
                                  "arguments": {"name": "", "steps": ["a"]}}})
    assert resp["error"]["code"] == -32602


# ── 第二波工具扩容（借鉴 aiduMEI 工具面，反查兰台已有服务）────────────────

@pytest.fixture()
def mcp_env():
    """内存 SQLite 真实建表 + FTS；仅 mock 外部依赖（embedding/向量存储）。"""
    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock):
        yield session_factory, engine


def _seed_memory(s, mid, content, lane="fact", status="active"):
    from lantai.models.tables import MemoryItem
    now = datetime.now(timezone.utc)
    s.add(MemoryItem(
        id=mid, memory_type="semantic", key=f"k-{mid}", content=content,
        lane=lane, status=status, importance=0.5, decay_score=1.0,
        decay_class="episodic", use_count=0,
        created_at=now - timedelta(days=3), updated_at=now - timedelta(hours=1),
    ))


def _call_tool(mod, name, args):
    resp = mod.handle({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})
    assert "error" not in resp, resp.get("error")
    return json.loads(resp["result"]["content"][0]["text"])


def test_mem_recent_active_only(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "发布会安排下周")
        _seed_memory(s, "m2", "已归档旧事", status="archived")
        s.commit()
    out = _call_tool(_load_mcp(), "mem_recent", {"limit": 10})
    assert [m["id"] for m in out["memories"]] == ["m1"]


def test_mem_stats_overview(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "发布会安排下周")
        _seed_memory(s, "m2", "规则：发布前先跑回滚演练", lane="rule")
        s.commit()
    out = _call_tool(_load_mcp(), "mem_stats", {})
    assert out["memories"]["total"] == 2
    assert out["memories"]["active"] == 2
    assert out["memories"]["by_lane"]["fact"] == 1
    assert out["memories"]["by_lane"]["rule"] == 1


def test_mem_health_ok(mcp_env):
    out = _call_tool(_load_mcp(), "mem_health", {})
    assert out["ok"] is True
    assert out["sqlite"] == "ok"
    assert "chromadb" in out


def test_autodream_report_dry_run_no_write(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "产品发布会定在周五下午两点")
        _seed_memory(s, "m2", "发布会需要提前一天彩排")
        _seed_memory(s, "m3", "无关记忆：今天天气不错", lane="rule")
        s.commit()
    out = _call_tool(_load_mcp(), "autodream_report", {})
    assert out["clusters"] >= 1
    assert out["created"] == 0  # dry-run 不写库


def test_autodream_trigger_then_proposals_list(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "产品发布会定在周五下午两点")
        _seed_memory(s, "m2", "发布会需要提前一天彩排")
        s.commit()
    out = _call_tool(_load_mcp(), "autodream_trigger", {})
    assert out["clusters"] >= 1
    assert out["created"] >= 1
    plist = _call_tool(_load_mcp(), "proposals_list", {"status": "pending", "limit": 10})
    assert len(plist["proposals"]) >= 1
    assert plist["proposals"][0]["decided_by"] == "autodream"


def test_proposal_decide_reject_records_reason(mcp_env):
    session_factory, _ = mcp_env
    from lantai.models.enums import ProposalStatus
    from lantai.models.tables import MemoryProposal
    with session_factory() as s:
        s.add(MemoryProposal(id="prop1", proposal_type="add", evidence_ids=["m1"],
                             reason="测试提案", proposed_patch={"content": "x"},
                             confidence=0.6, status=ProposalStatus.PENDING,
                             decided_by="autodream"))
        s.commit()
    out = _call_tool(_load_mcp(), "proposal_decide",
                     {"proposal_id": "prop1", "approve": False, "reason": "不想要"})
    assert out["ok"] is True
    with session_factory() as s:
        p = s.get(MemoryProposal, "prop1")
        assert p.status == ProposalStatus.REJECTED
        assert p.decision_reason == "不想要"


def test_proposal_decide_approve_applies(mcp_env):
    session_factory, _ = mcp_env
    from lantai.models.enums import ProposalStatus
    from lantai.models.tables import MemoryItem, MemoryProposal
    with session_factory() as s:
        _seed_memory(s, "m1", "发布会定在周五")
        s.add(MemoryProposal(
            id="prop2", proposal_type="add", evidence_ids=["m1"],
            reason="蒸馏", proposed_patch={
                "memory_type": "semantic", "key": "发布会议程",
                "content": "- 发布会定在周五\n- 提前一天彩排",
                "lane": "fact", "structure": {}},
            confidence=0.8, status=ProposalStatus.PENDING, decided_by="autodream"))
        s.commit()
    out = _call_tool(_load_mcp(), "proposal_decide", {"proposal_id": "prop2", "approve": True})
    assert out["ok"] is True
    with session_factory() as s:
        p = s.get(MemoryProposal, "prop2")
        assert p.status == ProposalStatus.APPLIED
        rows = s.exec(select(MemoryItem)).all()
        assert len(rows) == 2  # 原记忆 + 应用出的新记忆


# ── 第三波：树状图谱 + 技能结晶（v0.7）────────────────────

def test_tree_view_smoke(mcp_env):
    session_factory, _ = mcp_env
    from lantai.services.tree_service import add_node
    with session_factory() as s:
        add_node(s, "projects")
        _seed_memory(s, "m1", "发布会安排下周")
        s.commit()
        from lantai.services.tree_service import assign_memory
        assign_memory(s, "m1", "/projects")
    out = _call_tool(_load_mcp(), "tree_view", {})
    assert out["nodes"][0]["node_path"] == "/projects"
    assert out["nodes"][0]["attachments"]["direct"] == 1


def test_crystals_detect_dry_run_smoke(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "发布会在周五下午两点开始")
        _seed_memory(s, "m2", "发布会需要提前一天彩排")
        _seed_memory(s, "m3", "发布会结束后写复盘")
        s.commit()
    out = _call_tool(_load_mcp(), "crystals_detect", {"dry_run": True})
    assert out["clusters"] >= 1
    assert out["created"] == 0


# ── 第四波：工具面第三波（v0.8）─────────────────────────

def test_mem_usage_zero_fill(mcp_env):
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "今天的新记忆")
        s.commit()
    out = _call_tool(_load_mcp(), "mem_usage", {"days": 7})
    assert len(out["daily_new"]) == 7
    assert all(v >= 0 for v in out["daily_new"].values())


def test_core_memory_get_readonly(mcp_env):
    session_factory, _ = mcp_env
    from lantai.models.tables import CoreMemoryBlock
    with session_factory() as s:
        s.add(CoreMemoryBlock(id="core1", block="identity",
                              content="我是兰台用户", version=1))
        s.commit()
    out = _call_tool(_load_mcp(), "core_memory_get", {})
    assert any(b["block"] == "identity" for b in out["blocks"])


def test_verbatim_search_roundtrip(mcp_env):
    out = _call_tool(_load_mcp(), "verbatim_search", {"query": "发布会"})
    assert isinstance(out, list) or isinstance(out, dict)


def test_reflect_run_idle(mcp_env):
    """空库无候选 + 水位不足 -> skipped idle（不触发 LLM）。"""
    out = _call_tool(_load_mcp(), "reflect_run", {})
    assert out["ok"] is True
    assert out.get("skipped") == "idle"


def test_graph_view_roundtrip(mcp_env):
    """graph_view 只读：真实 SQLite 下返回节点/链接/统计形状（空库不炸）。"""
    session_factory, _ = mcp_env
    out = _call_tool(_load_mcp(), "graph_view", {})
    assert "nodes" in out and "links" in out and "stats" in out
    assert out["stats"] == {"lane_counts": {}, "node_type_counts": {}, "edge_counts": {}}


def test_graph_view_limit_invalid(mcp_env):
    """limit 越界 -> ValueError（宁 miss 不脏写式校验，不落任何状态）。"""
    mod = _load_mcp()
    resp = mod.handle({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                       "params": {"name": "graph_view", "arguments": {"limit": 9999}}})
    assert "error" in resp
