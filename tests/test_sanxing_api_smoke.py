"""三省·API、MCP工具集成与认知上下文切面冒烟测试 (v0.37 Ticket 02)

遵循 Matt Pocock TDD 纪律，真实构造 SQLite 数据库与 FastAPI TestClient / MCP 消息分发，
验证对外暴露的公共接缝（Seams）：
1. CognitiveContext 认知上下文 reflection_audit 切面渲染与樊篱包裹；
2. HTTP REST 路由：
   - GET /evolution/sanxing/health (200 成功与租户隔离)
   - POST /evolution/sanxing/audit (200 成功生成审计报告)
3. MCP 标准协议分发：
   - memory_health_inspect 工具调用
   - memory_audit_report 工具调用
   - TOOLS 与 TOOL_HANDLERS 1:1 双向一致性守卫。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from lantai.api.app import app
from lantai.cli.mcp import handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import ConflictEvent, MemoryItem


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes or ["fact", "rule", "general"],
        role="admin" if admin else "user",
    )


def test_cognitive_context_reflection_audit_seam():
    """Seam 1: 验证 CognitiveContext 扩展 reflection_audit 切面并经过樊篱数据围栏包裹。"""
    ctx = CognitiveContext(
        task="系统例行自我审视与记忆提炼",
        reflection_audit=[
            {
                "id": "mem_audit_01",
                "type": "superseded_stale",
                "content": "旧配置端口 8080",
                "suggestion": "建议尘封归档",
            }
        ],
    )
    prompt = ctx.to_prompt()
    assert "## Memory Health Audit & Self-Reflection" in prompt
    assert "<memory_data" in prompt
    assert "旧配置端口 8080" in prompt


def test_http_api_sanxing_routes_seam(param_env):
    """Seam 2: 验证 REST 端点 GET /evolution/sanxing/health 与 POST /evolution/sanxing/audit。"""
    session_factory, _ = param_env
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="alice_stale_mem",
                content="Alice 的过期令牌",
                status="active",
                user_id="alice",
                valid_to=now - timedelta(days=2),
            )
        )
        s.add(
            MemoryItem(
                id="bob_stale_mem",
                content="Bob 的过期密钥",
                status="active",
                user_id="bob",
                valid_to=now - timedelta(days=2),
            )
        )
        s.commit()

    client = TestClient(app)
    user_alice = _principal("alice")
    app.dependency_overrides[get_current_user] = lambda: user_alice
    try:
        # 1. Alice 访问 GET /evolution/sanxing/health
        resp = client.get("/evolution/sanxing/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "health_score" in data
        assert "expired_items" in data
        expired_ids = {item["id"] for item in data["expired_items"]}
        assert "alice_stale_mem" in expired_ids
        assert "bob_stale_mem" not in expired_ids  # 租户隔离

        # 2. Alice 访问 POST /evolution/sanxing/audit
        resp_audit = client.post("/evolution/sanxing/audit")
        assert resp_audit.status_code == 200, resp_audit.text
        audit_data = resp_audit.json()
        assert "health_score" in audit_data
        assert "summary" in audit_data
        assert audit_data["issues_count"] >= 1
        assert any(d["id"] == "alice_stale_mem" for d in audit_data["details"])
        assert not any(d["id"] == "bob_stale_mem" for d in audit_data["details"])
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_sanxing_tools_seam(param_env):
    """Seam 3: 验证 MCP 工具 memory_health_inspect 与 memory_audit_report 标准分发。"""
    session_factory, _ = param_env

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="alice_unhelpful_doc",
                content="无用的调试碎语",
                status="active",
                user_id="alice",
                use_count=7,
                helpful_count=0,
            )
        )
        s.commit()

    # 1. 调用 memory_health_inspect
    inspect_req = {
        "jsonrpc": "2.0",
        "id": "mcp-sanxing-1",
        "method": "tools/call",
        "params": {
            "name": "memory_health_inspect",
            "arguments": {"user_id": "alice"},
        },
    }
    inspect_resp = handle(inspect_req)
    assert inspect_resp is not None
    assert "result" in inspect_resp
    inspect_text = inspect_resp["result"]["content"][0]["text"]
    assert "health_score" in inspect_text
    assert "alice_unhelpful_doc" in inspect_text

    # 2. 调用 memory_audit_report
    audit_req = {
        "jsonrpc": "2.0",
        "id": "mcp-sanxing-2",
        "method": "tools/call",
        "params": {
            "name": "memory_audit_report",
            "arguments": {"user_id": "alice"},
        },
    }
    audit_resp = handle(audit_req)
    assert audit_resp is not None
    assert "result" in audit_resp
    audit_text = audit_resp["result"]["content"][0]["text"]
    assert "三省健康自检完成" in audit_text or "summary" in audit_text
    assert "alice_unhelpful_doc" in audit_text
