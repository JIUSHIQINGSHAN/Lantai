"""端到端不 mock 集成冒烟测试：磨勘服务层封装、REST 端点集成、MCP 96 工具池扩充与认知上下文切面融合 (Ticket 02)。

覆盖 4 个独立接缝：
1. 接缝 1 (服务层)：mokan_verify_service 与 mokan_audit_service 调度验证（真实 SQLite）；
2. 接缝 2 (REST 端点)：POST /mokan/verify 与 GET /mokan/audit 协议往返；
3. 接缝 3 (MCP 96 工具池)：MCP 协议往返、新增 mokan_cross_verify 工具与 96 工具 1:1 双向对齐守卫；
4. 接缝 4 (认知上下文切面)：CognitiveContext mokan_digest 切面渲染与樊篱防注入安全围栏包裹。
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
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.services.mokan_service import (
    mokan_audit_service,
    mokan_verify_service,
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


def test_seam1_service_layer_mokan(db_session: Session):
    """接缝 1：服务层 mokan_verify_service 与 mokan_audit_service 调度验证。"""
    principal = Principal(user_id="alice_mk", tenant_id="t_mk")

    mem1 = MemoryItem(
        id="mk_svc_1",
        content="生产环境已全面启用 TLS 1.3",
        confidence=0.8,
        status="active",
        user_id="alice_mk",
        tenant_id="t_mk",
    )
    mem2 = MemoryItem(
        id="mk_svc_2",
        content="网关安全证书链符合 TLS 1.3 规范要求",
        confidence=0.85,
        status="active",
        user_id="alice_mk",
        tenant_id="t_mk",
    )
    edge = MemoryEdge(
        id="mk_edge_1",
        source_memory_id="mk_svc_1",
        target_memory_id="mk_svc_2",
        relation="supports",
        user_id="alice_mk",
        tenant_id="t_mk",
    )
    db_session.add(mem1)
    db_session.add(mem2)
    db_session.add(edge)
    db_session.commit()

    # 1. 验证 mokan_verify_service
    res = mokan_verify_service(
        memory_ids=["mk_svc_1", "mk_svc_2"],
        session=db_session,
        principal=principal,
    )
    assert res["total_verified"] == 2
    assert res["verified_count"] == 2
    assert res["disputed_count"] == 0
    item1 = next(it for it in res["items"] if it["memory_id"] == "mk_svc_1")
    assert item1["verification_status"] == "verified"
    assert item1["trust_score"] > 0.8
    assert "<memory_data" in item1["fenced_content"]

    # 2. 验证 mokan_audit_service
    audit_res = mokan_audit_service(
        query="TLS",
        limit=10,
        session=db_session,
        principal=principal,
    )
    assert audit_res["total_audited"] >= 2
    assert audit_res["verified_count"] >= 2
    assert "<mokan_audit" in audit_res["fenced_content"]


def test_seam2_rest_endpoints():
    """接缝 2：REST 路由端点 POST /mokan/verify 与 GET /mokan/audit。"""
    client = TestClient(app)

    # 1. POST /mokan/verify（空列表或指定列表验证，期望 200 + 结构正确）
    r1 = client.post(
        "/mokan/verify",
        json={"memory_ids": ["non_existent_1"]},
        headers={"X-API-Key": "test"},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert "total_verified" in body1
    assert "items" in body1

    # 2. GET /mokan/audit
    r2 = client.get(
        "/mokan/audit",
        params={"query": "", "limit": 10},
        headers={"X-API-Key": "test"},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert "total_audited" in body2
    assert "verified_count" in body2
    assert "disputed_count" in body2


def test_seam3_mcp_96_tools_parity():
    """接缝 3：MCP 96 工具 mokan_cross_verify 注册 + TOOLS/TOOL_HANDLERS 1:1 对齐守卫。"""
    # 工具总数验证（96 工具池）
    assert len(TOOLS) == 96, f"Expected 96 tools, got {len(TOOLS)}"
    assert len(TOOL_HANDLERS) == len(TOOLS), (
        f"TOOLS ({len(TOOLS)}) ≠ TOOL_HANDLERS ({len(TOOL_HANDLERS)}): "
        f"missing={set(TOOLS) - set(TOOL_HANDLERS)}, "
        f"extra={set(TOOL_HANDLERS) - set(TOOLS)}"
    )

    # mokan_cross_verify 工具存在性验证
    assert "mokan_cross_verify" in TOOLS
    assert "mokan_cross_verify" in TOOL_HANDLERS

    # MCP 协议往返：verify 模式
    mcp_req = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "mokan_cross_verify",
            "arguments": {"action": "verify", "memory_ids": []},
        },
        "id": 1,
    }
    resp = handle(mcp_req)
    assert resp.get("error") is None, f"MCP error: {resp.get('error')}"
    content = resp.get("result", {}).get("content", [])
    assert any(c.get("type") == "text" for c in content)

    # MCP 协议往返：audit 模式
    mcp_audit_req = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "mokan_cross_verify",
            "arguments": {"action": "audit", "query": "", "limit": 5},
        },
        "id": 2,
    }
    resp_audit = handle(mcp_audit_req)
    assert resp_audit.get("error") is None, f"MCP error: {resp_audit.get('error')}"
    content_audit = resp_audit.get("result", {}).get("content", [])
    assert any(c.get("type") == "text" for c in content_audit)


def test_seam4_cognitive_context_mokan_digest():
    """接缝 4：CognitiveContext.mokan_digest 切面渲染与樊篱防注入安全围栏包裹。"""
    mk_digest = {
        "total_audited": 2,
        "verified_count": 1,
        "disputed_count": 1,
        "items": [
            {
                "memory_id": "mk_ctx_1",
                "content": "系统默认运行端口为 8000",
                "verification_status": "verified",
                "trust_score": 0.92,
                "fenced_content": '<memory_data id="mk_ctx_1" score="0.92">系统默认运行端口为 8000</memory_data>',
            },
            {
                "memory_id": "mk_ctx_2",
                "content": "系统默认运行端口为 9000",
                "verification_status": "disputed",
                "trust_score": 0.35,
                "fenced_content": '<memory_data id="mk_ctx_2" score="0.35">系统默认运行端口为 9000</memory_data>',
            },
        ],
    }

    ctx = CognitiveContext(task="测试磨勘切面注入", mokan_digest=mk_digest)
    prompt = ctx.to_prompt()

    # 1. 验证磨勘切面标题与指标
    assert "Cross-Verification & Trust Proof (磨勘 · 总计 2 | 证实 1 | 争议 1):" in prompt
    # 2. 验证证据条目状态与可信度
    assert "[磨勘·M1 (verified) [可信度: 0.92]]" in prompt
    assert "[磨勘·M2 (disputed) [可信度: 0.35]]" in prompt
    # 3. 验证樊篱围栏隔离
    assert '<memory_data id="mk_ctx_1" score="0.92">系统默认运行端口为 8000</memory_data>' in prompt
    assert '<memory_data id="mk_ctx_2" score="0.35">系统默认运行端口为 9000</memory_data>' in prompt
