"""格物·事件时间线与决策因果归因闭环冒烟测试 (Ticket 02)

基于真实 in-memory SQLite，不使用 mock。
覆盖：
1. trace_decision_provenance: 按 memory_id 回溯前置诱因、定位 root_causes、生成时序演进与溯源摘要；
2. trace_decision_provenance: 按 natural language query 检索目标决策并执行归因推导；
3. get_event_timeline: 基于 event_time 与因果拓扑生成时序事件流；
4. 租户行级 ACL 隔离与私有数据安全边界；
5. HTTP 路由 (/edges/provenance, /edges/timeline) 与 MCP 工具 (causal_trace, event_timeline) 真实调用。
"""

from datetime import UTC, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem
from lantai.services.edge_service import add_edge
from lantai.services.provenance_service import (
    get_event_timeline,
    trace_decision_provenance,
)


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_memories(
    param_env,
    items: list[tuple[str, str, str, datetime | None]],
):
    """(id, content, user_id, event_time) 批量灌入真实内存数据库"""
    session_factory, _ = param_env
    with session_factory() as s:
        for mid, content, uid, et in items:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
                    lane="fact",
                    status="active",
                    user_id=uid,
                    event_time=et,
                    event_time_precision="day" if et else "",
                )
            )
        s.commit()


def test_provenance_by_memory_id(param_env):
    """Seam 1: 按 memory_id 完整归因反查根因与因果链"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("bug_fts", "v0.3.2 发现 FTS schema 与时区真实 bug", "user_alice", t1),
            ("outage_mock", "原因在于纯 mock 单测跳过了真实执行逻辑导致漏测", "user_alice", t2),
            (
                "decision_nomock",
                "兰台工程纪律决定：核心函数必须至少有一个不 mock 的冒烟测试",
                "user_alice",
                t3,
            ),
        ],
    )
    user = _principal("user_alice")

    # 拓扑: bug_fts -> causes -> outage_mock -> causes -> decision_nomock
    add_edge("bug_fts", "outage_mock", relation="causes", confidence=0.95, principal=user)
    add_edge("outage_mock", "decision_nomock", relation="causes", confidence=0.98, principal=user)

    result = trace_decision_provenance(memory_id="decision_nomock", principal=user)

    assert result["found"] is True
    assert result["target_id"] == "decision_nomock"
    assert len(result["causal_chain"]) == 2
    assert result["root_cause_id"] == "bug_fts"
    assert "核心函数必须至少有一个不 mock" in result["narrative"]
    assert "FTS schema 与时区真实 bug" in result["narrative"]
    assert len(result["timeline"]) == 3
    # 验证时序正序排列 (t1 -> t2 -> t3)
    assert result["timeline"][0]["memory_id"] == "bug_fts"
    assert result["timeline"][1]["memory_id"] == "outage_mock"
    assert result["timeline"][2]["memory_id"] == "decision_nomock"


def test_provenance_by_query_search(param_env):
    """Seam 2: 按自然语言查询定位目标决策并执行归因推导"""
    t1 = datetime(2026, 9, 10, tzinfo=UTC)
    t2 = datetime(2026, 9, 15, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("cause_oom", "生产服务器频繁发生 OOM 内存溢出", "user_alice", t1),
            ("policy_limit", "制定全局知识注入 4~5 条硬顶抗干扰策略", "user_alice", t2),
        ],
    )
    user = _principal("user_alice")
    add_edge("cause_oom", "policy_limit", relation="causes", confidence=0.9, principal=user)

    # 通过自然语言 query 检索到 policy_limit 并溯源
    result = trace_decision_provenance(query="知识注入硬顶抗干扰策略", principal=user)
    assert result["found"] is True
    assert result["target_id"] == "policy_limit"
    assert result["root_cause_id"] == "cause_oom"
    assert len(result["causal_chain"]) == 1


def test_event_timeline(param_env):
    """Seam 3: 基于事件时间与因果拓扑生成时序流"""
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    t3 = datetime(2026, 3, 1, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("ev_1", "系统完成架构奠基", "user_alice", t1),
            ("ev_2", "引入探颐主动求证机制", "user_alice", t2),
            ("ev_3", "落地格物因果溯源引擎", "user_alice", t3),
        ],
    )
    user = _principal("user_alice")
    add_edge("ev_1", "ev_2", relation="leads_to", confidence=0.8, principal=user)
    add_edge("ev_2", "ev_3", relation="leads_to", confidence=0.85, principal=user)

    timeline_res = get_event_timeline(memory_id="ev_2", principal=user)
    events = timeline_res["events"]
    assert len(events) >= 3
    # 保证严格升序排列
    times = [e["timestamp"] for e in events if e.get("timestamp")]
    assert times == sorted(times)
    assert any(e["memory_id"] == "ev_3" for e in events)


def test_provenance_acl_isolation(param_env):
    """Seam 4: 租户行级 ACL 隔离安全校验"""
    t1 = datetime(2026, 5, 1, tzinfo=UTC)
    t2 = datetime(2026, 5, 2, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("bob_secret_bug", "Bob 的内部系统出现漏洞", "user_bob", t1),
            ("alice_decision", "Alice 决定升级安全防护", "user_alice", t2),
        ],
    )
    user_alice = _principal("user_alice")

    # Bob 节点私有，Alice 查 Bob 的记忆直接不可见
    result_bob = trace_decision_provenance(memory_id="bob_secret_bug", principal=user_alice)
    assert result_bob["found"] is False

    # 即使底层存在跨租户边，Alice 查自己决策时也不会泄漏 Bob 节点
    add_edge("bob_secret_bug", "alice_decision", relation="causes", confidence=0.9, principal=None)
    result_alice = trace_decision_provenance(memory_id="alice_decision", principal=user_alice)
    assert result_alice["found"] is True
    # Bob 的私有节点被隔离阻断，不会出现在 Alice 的根因与时间线中
    assert "Bob 的内部系统" not in result_alice["narrative"]
    assert result_alice["root_cause_id"] == "alice_decision"


def test_api_and_mcp_integration(param_env):
    """Seam 5: API 路由与 MCP 工具真实调用验证"""
    from lantai.api.app import app
    from lantai.cli.mcp import handle

    t1 = datetime(2026, 8, 1, tzinfo=UTC)
    t2 = datetime(2026, 8, 2, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("inc_1", "高并发压测触发限流保护", "user_alice", t1),
            ("inc_2", "自适应扩大连接池容量", "user_alice", t2),
        ],
    )
    user = _principal("user_alice")
    add_edge("inc_1", "inc_2", relation="causes", confidence=0.9, principal=user)

    # 1. API 测试: GET /edges/provenance 与 GET /edges/timeline
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp_prov = client.get("/edges/provenance?memory_id=inc_2")
        assert resp_prov.status_code == 200
        data_prov = resp_prov.json()
        assert data_prov["found"] is True
        assert data_prov["root_cause_id"] == "inc_1"

        resp_tl = client.get("/edges/timeline?memory_id=inc_2")
        assert resp_tl.status_code == 200
        data_tl = resp_tl.json()
        assert "events" in data_tl
        assert len(data_tl["events"]) >= 2
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 2. MCP 测试: causal_trace 与 event_timeline
    mcp_resp_trace = handle(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "causal_trace",
                "arguments": {"memory_id": "inc_2", "user_id": "user_alice"},
            },
        }
    )
    assert "error" not in mcp_resp_trace
    trace_payload = mcp_resp_trace["result"]["content"][0]["text"]
    assert "高并发压测触发限流保护" in trace_payload

    mcp_resp_tl = handle(
        {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "event_timeline",
                "arguments": {"memory_id": "inc_2", "user_id": "user_alice"},
            },
        }
    )
    assert "error" not in mcp_resp_tl
    tl_payload = mcp_resp_tl["result"]["content"][0]["text"]
    assert "自适应扩大连接池容量" in tl_payload
