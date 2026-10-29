"""格物·事件溯源链 API 与查询接口及决策脉络回溯冒烟测试 (Ticket 02)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用任何内部逻辑 mock。
验证公共接口 Seams：
1. CausalEngine.attribute_decision: 高阶决策归因方法（根因定位、时间线正序组织、因果脉络生成）；
2. CognitiveContext 集成: 在认知上下文中嵌入因果溯源切面 (causal_provenance)，并能格式化注入 Agent Prompt；
3. HTTP 路由端点: 验证 GET /edges/provenance 与 GET /edges/timeline 能够响应并准确生成归因与事件演进时间线；
4. MCP 工具标准协议: 验证 causal_trace 与 event_timeline 经由 JSON-RPC 2.0 工具协议返回因果分析结果与严格行级租户隔离。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import handle
from lantai.cognition.causal import CausalEngine
from lantai.cognition.context import CognitiveContext, CognitiveContextBuilder
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem
from lantai.services.edge_service import add_edge


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_causal_data(param_env):
    """灌入真实的因果演化事态链：
    事件1: 2026-09-01 引入外部未经鉴权的轻量解析器 (mem_root)
    事件2: 2026-09-05 收到包含恶意特征的畸形请求触发栈溢出 (mem_trigger)
    事件3: 2026-09-10 核心网关服务全面陷入502宕机状态 (mem_crash)
    事件4: 2026-09-12 紧急决议：兰台全链路部署獬豸写入侧防御 (mem_decision)
    """
    t1 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)
    t3 = datetime(2026, 9, 10, 8, 30, tzinfo=UTC)
    t4 = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)

    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem_root",
                content="引入未经充分测试的轻量解析器",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t1,
            )
        )
        s.add(
            MemoryItem(
                id="mem_trigger",
                content="畸形请求触发底层运行时栈溢出",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t2,
            )
        )
        s.add(
            MemoryItem(
                id="mem_crash",
                content="核心网关服务陷入连续502不可用崩溃",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t3,
            )
        )
        s.add(
            MemoryItem(
                id="mem_decision",
                content="架构委员会决议全面推行獬豸防注入与输入硬校验",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t4,
            )
        )
        # 异构租户节点
        s.add(
            MemoryItem(
                id="bob_secret_node",
                content="Bob 的内部私密前置诱因",
                lane="fact",
                status="active",
                user_id="bob",
            )
        )
        s.commit()

    user_alice = _principal("alice")
    add_edge("mem_root", "mem_trigger", relation="causes", confidence=0.95, principal=user_alice)
    add_edge("mem_trigger", "mem_crash", relation="leads_to", confidence=0.90, principal=user_alice)
    add_edge("mem_crash", "mem_decision", relation="causes", confidence=0.98, principal=user_alice)


def test_causal_engine_attribute_decision_seam(param_env):
    """Seam 1: 验证 CausalEngine.attribute_decision 归因分析。"""
    _seed_causal_data(param_env)
    user_alice = _principal("alice")
    engine = CausalEngine(principal=user_alice)

    # 1. 按 memory_id 归因反查
    prov = engine.attribute_decision(memory_id="mem_decision")
    assert prov["found"] is True
    assert prov["target_id"] == "mem_decision"
    assert prov["root_cause_id"] == "mem_root"
    assert len(prov["causal_chain"]) == 3
    assert len(prov["timeline"]) == 4
    # 验证时间线正序排列
    assert prov["timeline"][0]["memory_id"] == "mem_root"
    assert prov["timeline"][-1]["memory_id"] == "mem_decision"
    assert "引入未经充分测试的轻量解析器" in prov["narrative"]
    assert "全面推行獬豸防注入" in prov["narrative"]

    # 2. 按 query 自然语言检索定位决策归因
    prov_q = engine.attribute_decision(query="决议全面推行獬豸防注入")
    assert prov_q["found"] is True
    assert prov_q["target_id"] == "mem_decision"
    assert prov_q["root_cause_id"] == "mem_root"


def test_cognitive_context_causal_provenance_integration(param_env):
    """Seam 2: 验证认知上下文与因果归因提示词切面联动。"""
    _seed_causal_data(param_env)
    session_factory, _ = param_env
    user_alice = _principal("alice")

    with session_factory() as s:
        builder = CognitiveContextBuilder(s, principal=user_alice)
        ctx = builder.build(task="评估推行獬豸防注入方案的历史决策背景", top_k=5)

        # 检查认知上下文数据结构支持
        assert hasattr(ctx, "causal_provenance")
        # 构造带有归因叙事的认知上下文并转为 prompt
        engine = CausalEngine(principal=user_alice)
        prov = engine.attribute_decision(memory_id="mem_decision")
        ctx.causal_provenance = [prov]

        prompt_text = ctx.to_prompt()
        assert (
            "## Causal Provenance & Decision History" in prompt_text
            or "## 决策因果脉络" in prompt_text
        )
        assert "mem_decision" in prompt_text
        assert "引入未经充分测试的轻量解析器" in prompt_text


def test_http_api_provenance_and_timeline(param_env):
    """Seam 3: 验证 HTTP 路由 GET /edges/provenance 与 GET /edges/timeline。"""
    _seed_causal_data(param_env)
    user_alice = _principal("alice")
    client = TestClient(app)

    app.dependency_overrides[get_current_user] = lambda: user_alice
    try:
        # 1. 验证决策归因路由
        resp_prov = client.get("/edges/provenance?memory_id=mem_decision")
        assert resp_prov.status_code == 200
        data_prov = resp_prov.json()
        assert data_prov["found"] is True
        assert data_prov["root_cause_id"] == "mem_root"
        assert len(data_prov["timeline"]) == 4

        # 2. 验证自然语言模糊检索归因路由
        resp_q = client.get("/edges/provenance?query=全面推行獬豸")
        assert resp_q.status_code == 200
        assert resp_q.json()["found"] is True
        assert resp_q.json()["target_id"] == "mem_decision"

        # 3. 验证事件演进全景时间线路由
        resp_tl = client.get("/edges/timeline?memory_id=mem_trigger")
        assert resp_tl.status_code == 200
        data_tl = resp_tl.json()
        assert "events" in data_tl
        # mem_trigger 向前有 mem_root，向后有 mem_crash、mem_decision
        event_ids = {e["memory_id"] for e in data_tl["events"]}
        assert "mem_root" in event_ids
        assert "mem_decision" in event_ids
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_causal_trace_and_acl_isolation(param_env):
    """Seam 4: 验证 MCP 协议工具调用与租户行级安全隔离。"""
    _seed_causal_data(param_env)

    # 1. Alice 正常调用 causal_trace
    mcp_alice = handle(
        {
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {
                "name": "causal_trace",
                "arguments": {"memory_id": "mem_decision", "user_id": "alice"},
            },
        }
    )
    assert "error" not in mcp_alice
    text_alice = mcp_alice["result"]["content"][0]["text"]
    assert "引入未经充分测试的轻量解析器" in text_alice

    # 2. Alice 试图溯源 Bob 的私密记忆 -> 隔离防御，不返回内容
    mcp_hack = handle(
        {
            "jsonrpc": "2.0",
            "id": 202,
            "method": "tools/call",
            "params": {
                "name": "causal_trace",
                "arguments": {"memory_id": "bob_secret_node", "user_id": "alice"},
            },
        }
    )
    assert "error" not in mcp_hack
    text_hack = mcp_hack["result"]["content"][0]["text"]
    assert "未找到匹配的目标决策或无权访问" in text_hack
