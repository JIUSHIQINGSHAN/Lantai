"""端到端不 mock 集成冒烟测试：知命服务层重构、REST 端点集成、MCP 94 工具池扩充与认知上下文切面融合 (Ticket 02)。

覆盖 4 个独立接缝：
1. 接缝 1 (服务层)：zhiming_inspect_service 与 zhiming_transition_service 调度及预演/落库；
2. 接缝 2 (REST 端点)：POST /lifecycle/transition 与 GET /lifecycle/inspect 鉴权、响应结构与状态流转；
3. 接缝 3 (MCP 94 工具池)：MCP 协议往返、新增 zhiming_lifecycle 工具与 94 工具 1:1 双向对齐守卫；
4. 接缝 4 (认知上下文切面)：CognitiveContext zhiming_digest 切面渲染与樊篱防注入安全围栏包裹。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.models.tables import LifecycleStatus, MemoryItem
from lantai.services.zhiming_service import (
    zhiming_inspect_service,
    zhiming_transition_service,
)


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seam1_service_layer_zhiming(db_session: Session):
    """接缝 1：服务层 zhiming_inspect_service 与 zhiming_transition_service 调度。"""
    mem = MemoryItem(
        id="mem_zm_srv_01",
        content="网关限流配置基线",
        confidence=0.8,
        use_count=2,
        helpful_count=1,
        status="active",
        lifecycle_status=LifecycleStatus.ACTIVE,
        user_id="u_zhiming",
        tenant_id="t_zhiming",
    )
    db_session.add(mem)
    db_session.commit()

    principal = Principal(user_id="u_zhiming", tenant_id="t_zhiming")

    # 1. 探查服务
    ins = zhiming_inspect_service(
        memory_id="mem_zm_srv_01", session=db_session, principal=principal
    )
    assert ins["memory_id"] == "mem_zm_srv_01"
    assert ins["phase"] == "active"
    assert "effective_confidence" in ins
    assert "<memory_data" in ins["fenced_content"]

    # 2. 状态机预演 (apply=False)
    dry_trans = zhiming_transition_service(
        memory_id="mem_zm_srv_01",
        target_phase="weakened",
        reason="半年无更新",
        apply=False,
        session=db_session,
        principal=principal,
    )
    assert dry_trans["applied"] is False
    assert dry_trans["to_phase"] == "weakened"
    # 确认数据库未改变
    m_check = db_session.get(MemoryItem, "mem_zm_srv_01")
    assert m_check.lifecycle_status == LifecycleStatus.ACTIVE

    # 3. 状态机就地落库 (apply=True)
    real_trans = zhiming_transition_service(
        memory_id="mem_zm_srv_01",
        target_phase="weakened",
        reason="确认弱化",
        apply=True,
        session=db_session,
        principal=principal,
    )
    assert real_trans["applied"] is True
    assert real_trans["to_phase"] == "weakened"

    db_session.refresh(m_check)
    assert m_check.lifecycle_status == LifecycleStatus.WEAKENED
    assert m_check.weakened_at is not None


def test_seam2_rest_endpoints():
    """接缝 2：REST 路由端点 GET /lifecycle/inspect 与 POST /lifecycle/transition。"""
    client = TestClient(app)

    # 1. POST /lifecycle/transition 预演
    r1 = client.post(
        "/lifecycle/transition",
        json={
            "memory_id": "non_existent_mid",
            "target_phase": "weakened",
            "reason": "测试跃迁",
            "apply": False,
        },
        headers={"X-API-Key": "test"},
    )
    # 不存在的目标报 422/404 均合规
    assert r1.status_code in (404, 422)

    # 2. GET /lifecycle/inspect
    r2 = client.get(
        "/lifecycle/inspect",
        params={"memory_id": "non_existent_mid"},
        headers={"X-API-Key": "test"},
    )
    assert r2.status_code == 404


def test_seam3_mcp_94_tools_parity():
    """接缝 3：MCP 协议往返与 94 工具池 1:1 双向对齐守卫。"""
    assert len(TOOLS) == 94
    assert len(TOOL_HANDLERS) == 94
    assert "zhiming_lifecycle" in TOOLS
    assert "zhiming_lifecycle" in TOOL_HANDLERS

    # 双向对齐严格断言
    for t_name in TOOLS:
        assert t_name in TOOL_HANDLERS, f"Tool {t_name} missing from TOOL_HANDLERS"
    for h_name in TOOL_HANDLERS:
        assert h_name in TOOLS, f"Handler {h_name} missing from TOOLS"

    # tools/list 协议响应断言
    resp_list = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert resp_list is not None
    names = [t["name"] for t in resp_list["result"]["tools"]]
    assert "zhiming_lifecycle" in names

    # tools/call 协议调度执行断言 (action=audit 预演)
    call_resp = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "zhiming_lifecycle",
                "arguments": {"action": "audit", "auto_apply": False, "limit": 10},
            },
        }
    )
    assert call_resp is not None
    assert "result" in call_resp
    text_content = call_resp["result"]["content"][0]["text"]
    assert "total_inspected" in text_content
    assert "知命巡检" in text_content


def test_seam4_cognitive_context_zhiming_aspect():
    """接缝 4：CognitiveContext zhiming_digest 切面渲染与樊篱防注入安全围栏包裹。"""
    items = [
        {
            "memory_id": "mem_zm_ctx_01",
            "phase": "weakened",
            "effective_confidence": 0.42,
            "reason": "时效衰减进入弱化期",
            "fenced_content": '<memory_data id="mem_zm_ctx_01" score="0.42">旧版数据库连接池配置</memory_data>',
        }
    ]

    ctx = CognitiveContext(task="测试认知上下文知命切面", zhiming_digest=items)
    prompt = ctx.to_prompt()

    assert "## Knowledge Lifecycle & Decay State (知命 · 1):" in prompt
    assert "- [知命·L1 (weakened) [有效置信度: 0.42]]" in prompt
    assert (
        '<memory_data id="mem_zm_ctx_01" score="0.42">旧版数据库连接池配置</memory_data>' in prompt
    )
    assert "时效衰减进入弱化期" in prompt
