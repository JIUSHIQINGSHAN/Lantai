"""端到端不 mock 集成冒烟测试：三省服务层重构、REST 端点集成、MCP 92 工具池扩充与认知上下文切面融合 (Ticket 02)。

覆盖 4 个独立接缝：
1. 接缝 1 (服务层)：sanxing_remedy_service 与 sanxing_diagnose_service 调度及预演/落库；
2. 接缝 2 (REST 端点)：POST /sanxing/remedy 与 GET /sanxing/diagnose 鉴权、响应结构与状态流转；
3. 接缝 3 (MCP 92 工具池)：MCP 协议往返、新增 sanxing_remedy 工具与 92 工具 1:1 双向对齐守卫；
4. 接缝 4 (认知上下文切面)：CognitiveContext sanxing_digest 切面渲染与樊篱防注入安全围栏。
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
from lantai.models.tables import MemoryItem
from lantai.services.sanxing_service import (
    sanxing_diagnose_service,
    sanxing_remedy_service,
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


def test_seam1_service_layer_sanxing(db_session: Session):
    """接缝 1：服务层 sanxing_diagnose_service 与 sanxing_remedy_service 调度。"""
    # 构造一条低效碎片记忆
    mem = MemoryItem(
        id="mem_sx_srv_01",
        content="临时环境调试废弃指令",
        use_count=6,
        helpful_count=0,
        importance=0.5,
        status="active",
        user_id="u_sanxing",
        tenant_id="t_sanxing",
    )
    db_session.add(mem)
    db_session.commit()

    principal = Principal(user_id="u_sanxing", tenant_id="t_sanxing")

    # 1. 诊断服务
    diag = sanxing_diagnose_service(session=db_session, principal=principal)
    assert diag["health_score"] == 95.0
    assert diag["issues_count"] == 1
    assert "三省" in diag["summary"]

    # 2. 自愈预演服务（auto_apply=False）
    preview = sanxing_remedy_service(auto_apply=False, session=db_session, principal=principal)
    assert preview["total_defects"] == 1
    assert preview["applied_count"] == 0
    assert preview["health_score_before"] == 95.0
    assert preview["health_score_after"] == 95.0
    # 验证未写库
    m_check = db_session.get(MemoryItem, "mem_sx_srv_01")
    assert m_check.importance == 0.5

    # 3. 自愈落库执行服务（auto_apply=True）
    applied = sanxing_remedy_service(auto_apply=True, session=db_session, principal=principal)
    assert applied["total_defects"] == 1
    assert applied["applied_count"] == 1
    assert applied["health_score_before"] == 95.0
    assert applied["health_score_after"] == 100.0
    # 验证真实写库
    m_updated = db_session.get(MemoryItem, "mem_sx_srv_01")
    assert m_updated.importance == 0.1


def test_seam2_rest_endpoints(monkeypatch):
    """接缝 2：REST 路由端点 POST /sanxing/remedy 与 GET /sanxing/diagnose。"""
    client = TestClient(app)

    # 1. GET /sanxing/diagnose
    r1 = client.get("/sanxing/diagnose", headers={"X-API-Key": "test"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert "health_score" in d1
    assert "issues_count" in d1

    # 2. POST /sanxing/remedy (预演)
    r2 = client.post(
        "/sanxing/remedy",
        json={"auto_apply": False},
        headers={"X-API-Key": "test"},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert "total_defects" in d2
    assert "applied_count" in d2
    assert "health_score_before" in d2
    assert "health_score_after" in d2


def test_seam3_mcp_tools_parity():
    """接缝 3：MCP 协议往返与工具池 1:1 双向对齐守卫（工具数随版本扩容，不硬编码快照）。"""
    assert len(TOOLS) >= 92
    assert len(TOOL_HANDLERS) >= 92
    assert len(TOOLS) == len(TOOL_HANDLERS)
    assert "sanxing_remedy" in TOOLS
    assert "sanxing_remedy" in TOOL_HANDLERS

    # 双向对齐严格断言
    for t_name in TOOLS:
        assert t_name in TOOL_HANDLERS, f"Tool {t_name} missing from TOOL_HANDLERS"
    for h_name in TOOL_HANDLERS:
        assert h_name in TOOLS, f"Handler {h_name} missing from TOOLS"

    # tools/list 协议响应断言
    resp_list = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert resp_list is not None
    names = [t["name"] for t in resp_list["result"]["tools"]]
    assert "sanxing_remedy" in names

    # tools/call 协议调度执行断言
    call_resp = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "sanxing_remedy",
                "arguments": {"auto_apply": False},
            },
        }
    )
    assert call_resp is not None
    assert "result" in call_resp
    text_content = call_resp["result"]["content"][0]["text"]
    assert "total_defects" in text_content
    assert "三省" in text_content


def test_seam4_cognitive_context_sanxing_aspect():
    """接缝 4：CognitiveContext sanxing_digest 切面渲染与樊篱防注入安全围栏包裹。"""
    remedies = [
        {
            "target_id": "mem_sx_ctx_01",
            "defect_type": "superseded_stale",
            "recommended_action": "supersede_retract",
            "risk_level": "low",
            "reason": "已由新规范替代",
            "fenced_content": '<memory_data id="mem_sx_ctx_01">旧版网关路由规范</memory_data>',
        }
    ]
    ctx = CognitiveContext(task="测试三省切面", sanxing_digest=remedies)
    prompt = ctx.to_prompt()

    assert "## Memory Health Remediation & Reflection (三省 · 1):" in prompt
    assert "- [三省·S1 (supersede_retract) [缺陷: superseded_stale] [风险: low]]" in prompt
    assert '<memory_data id="mem_sx_ctx_01">旧版网关路由规范</memory_data>' in prompt
    assert "已由新规范替代" in prompt
