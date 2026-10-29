"""格物·反事实 API、MCP 工具集成与认知上下文推演切面冒烟测试 (Ticket 02)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用 mock。
覆盖公共接口 Seams：
1. 服务层接口：evaluate_counterfactual_service 与 project_causal_impact_service 真实推演与 ACL 隔离；
2. 认知上下文：CognitiveContext 对反事实与前瞻推演切面的集成及 to_prompt() 樊篱安全包裹；
3. HTTP 路由端点：POST /edges/counterfactual 与 POST /edges/project-impact；
4. MCP 工具标准协议：causal_counterfactual 与 causal_project_impact 经由 JSON-RPC 2.0 正确返回及行级隔离。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem
from lantai.services.edge_service import add_edge
from lantai.services.provenance_service import (
    evaluate_counterfactual_service,
    project_causal_impact_service,
)


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_causal_data(param_env):
    """构建用于反事实与前瞻推演的因果图：
    root: 2026-10-01 引入未验证解析器
    trigger: 2026-10-02 触发异常输入
    alt_trigger: 2026-10-02 内部定时任务并发触发
    target: 2026-10-03 服务网关熔断不可用
    next_effect: 2026-10-04 监控系统大面积告警
    """
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
    t4 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    t5 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)

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
                content="外部畸形请求触发底层运行时溢出",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t2,
            )
        )
        s.add(
            MemoryItem(
                id="mem_alt_trigger",
                content="内部批处理任务死锁抢占资源",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t3,
            )
        )
        s.add(
            MemoryItem(
                id="mem_target",
                content="核心网关服务全面不可用",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t4,
            )
        )
        s.add(
            MemoryItem(
                id="mem_next_effect",
                content="监控大盘触发P0级全员短信告警",
                lane="fact",
                status="active",
                user_id="alice",
                event_time=t5,
            )
        )
        s.commit()

    user_alice = _principal("alice")
    # 建立主链
    add_edge("mem_root", "mem_trigger", relation="causes", confidence=0.9, principal=user_alice)
    add_edge(
        "mem_trigger", "mem_target", relation="leads_to", confidence=0.95, principal=user_alice
    )
    add_edge(
        "mem_target", "mem_next_effect", relation="causes", confidence=0.85, principal=user_alice
    )
    # 建立替代支路
    add_edge("mem_root", "mem_alt_trigger", relation="causes", confidence=0.7, principal=user_alice)
    add_edge(
        "mem_alt_trigger", "mem_target", relation="leads_to", confidence=0.8, principal=user_alice
    )


def test_service_layer_counterfactual_and_impact(param_env):
    """Seam 1: 验证服务层 evaluate_counterfactual_service 与 project_causal_impact_service。"""
    _seed_causal_data(param_env)
    user_alice = _principal("alice")

    # 1. 反事实推演：阻断 mem_trigger，由于有 mem_alt_trigger 替代，is_necessary 应为 False
    res = evaluate_counterfactual_service(
        target_id="mem_target",
        intervened_node_id="mem_trigger",
        principal=user_alice,
    )
    assert res["found"] is True
    assert res["target_id"] == "mem_target"
    assert res["is_necessary"] is False
    assert len(res["alternative_paths"]) >= 1

    # 2. 前瞻影响面推演：从 mem_target 出发，预测下游 mem_next_effect
    impact = project_causal_impact_service(
        memory_id="mem_target",
        max_depth=3,
        principal=user_alice,
    )
    assert impact["found"] is True
    assert impact["start_node_id"] == "mem_target"
    impacted_ids = [n["node_id"] for n in impact["impacted_nodes"]]
    assert "mem_next_effect" in impacted_ids


def test_cognitive_context_counterfactual_rendering(param_env):
    """Seam 2: 验证认知上下文对反事实推演切面的集成与樊篱包裹。"""
    _seed_causal_data(param_env)
    user_alice = _principal("alice")

    res = evaluate_counterfactual_service(
        target_id="mem_target",
        intervened_node_id="mem_trigger",
        principal=user_alice,
    )

    ctx = CognitiveContext(task="排查网关不可用的单点脆弱性")
    ctx.causal_provenance = [res]
    prompt_text = ctx.to_prompt()

    assert "## Causal Provenance & Decision History" in prompt_text
    assert "mem_target" in prompt_text
    assert "<memory_data>" in prompt_text
    assert "</memory_data>" in prompt_text


def test_http_api_counterfactual_and_impact(param_env):
    """Seam 3: 验证 HTTP POST /edges/counterfactual 与 POST /edges/project-impact。"""
    _seed_causal_data(param_env)
    user_alice = _principal("alice")
    client = TestClient(app)

    app.dependency_overrides[get_current_user] = lambda: user_alice
    try:
        # 1. 验证反事实接口
        resp1 = client.post(
            "/edges/counterfactual",
            json={"target_memory_id": "mem_target", "intervened_node_id": "mem_trigger"},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["found"] is True
        assert data1["is_necessary"] is False

        # 2. 验证前瞻影响面接口
        resp2 = client.post(
            "/edges/project-impact",
            json={"memory_id": "mem_target", "max_depth": 5},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["found"] is True
        assert len(data2["impacted_nodes"]) >= 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_counterfactual_tools_and_acl(param_env):
    """Seam 4 & 5: 验证 MCP causal_counterfactual 与 causal_project_impact 工具及行级租户隔离。"""
    _seed_causal_data(param_env)

    # 1. Alice 正常调用 causal_counterfactual
    resp_cf = handle(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "causal_counterfactual",
                "arguments": {
                    "target_memory_id": "mem_target",
                    "intervened_node_id": "mem_trigger",
                    "user_id": "alice",
                },
            },
        }
    )
    assert "error" not in resp_cf
    text_cf = resp_cf["result"]["content"][0]["text"]
    assert "mem_target" in text_cf
    assert "is_necessary" in text_cf

    # 2. Alice 正常调用 causal_project_impact
    resp_imp = handle(
        {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "causal_project_impact",
                "arguments": {
                    "memory_id": "mem_target",
                    "user_id": "alice",
                },
            },
        }
    )
    assert "error" not in resp_imp
    text_imp = resp_imp["result"]["content"][0]["text"]
    assert "mem_next_effect" in text_imp

    # 3. 租户行级安全隔离校验：Eve 试图推演 Alice 的数据
    resp_eve = handle(
        {
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {
                "name": "causal_counterfactual",
                "arguments": {
                    "target_memory_id": "mem_target",
                    "intervened_node_id": "mem_trigger",
                    "user_id": "eve",
                },
            },
        }
    )
    assert "error" not in resp_eve
    text_eve = resp_eve["result"]["content"][0]["text"]
    assert '"found": false' in text_eve or '"found":false' in text_eve
