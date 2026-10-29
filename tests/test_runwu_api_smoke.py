"""
tests/test_runwu_api_smoke.py
v0.39「润物」API 端点与 MCP 工具冒烟测试（全不 mock）

遵循 AGENTS.md 测试纪律：
- 真实调用 FastAPI TestClient 与真实 SQLite 数据库；
- 真实分发 MCP jsonrpc tools/call 请求；
- 绝不 mock 核心函数内部计算。
"""

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.api.app import app
from lantai.cli import mcp as mcp_module
from lantai.core.auth import Principal, get_current_user
from lantai.core.ids import new_id
from lantai.models.tables import CognitiveRole, FailureRecord, MemoryItem
from lantai.storage import db as db_module


@pytest.fixture(name="client_and_db")
def client_and_db_fixture():
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(test_engine)

    def _get_test_session():
        return Session(test_engine)

    original_engine = db_module.engine
    original_get_session = db_module.get_session

    db_module.engine = test_engine
    db_module.get_session = _get_test_session
    app.dependency_overrides[db_module.get_session] = _get_test_session

    # 插入测试规则与已知教训
    with Session(test_engine) as session:
        r = MemoryItem(
            id=new_id("mem"),
            content="执行关键操作前必须核验身份凭证与租户归属",
            role=CognitiveRole.RULE,
            status="active",
            confidence=0.92,
            user_id="default",
            tenant_id="test_tenant",
        )
        f = FailureRecord(
            id=new_id("fl"),
            task="未验证凭证直接执行删除操作",
            action="delete_record",
            expected="成功安全删除",
            actual="越权删除了其他租户的数据",
            lesson="写操作与删操作必须严格校验租户隔离权限",
            severity=0.9,
        )
        session.add(r)
        session.add(f)
        session.commit()

    test_principal = Principal(user_id="default", tenant_id="test_tenant", role="user")
    app.dependency_overrides[get_current_user] = lambda: test_principal

    try:
        with TestClient(app) as client:
            yield client, test_engine
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(db_module.get_session, None)
        db_module.engine = original_engine
        db_module.get_session = original_get_session


def test_http_cognitive_summary_endpoint(client_and_db):
    """验证 GET /cognitive/summary RESTful 端点正常返回润物认知摘要。"""
    client, _ = client_and_db

    resp = client.get(
        "/cognitive/summary",
        params={"task": "执行用户数据删除操作", "max_rules": 2, "max_failures": 1},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["task"] == "执行用户数据删除操作"
    assert "rules" in data
    assert "failures" in data
    assert "summary" in data
    assert "probes" in data
    # 验证提取到的规则与失败教训包含关键内容
    assert any("核验身份凭证" in r["content"] for r in data["rules"])
    assert any("租户隔离" in f["lesson"] for f in data["failures"])
    assert "相关规则:" in data["summary"]
    assert "已知教训:" in data["summary"]


def test_mcp_cognitive_summary_tool():
    """验证 MCP 工具 cognitive_summary 可被正常列出并分发调用。"""
    import json

    # 1. 验证在 tools/list 中
    resp_list = mcp_module.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = resp_list["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "cognitive_summary" in tool_names

    # 2. 验证 tools/call 分发调用
    resp_call = mcp_module.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "cognitive_summary",
                "arguments": {
                    "task": "部署新服务并配置数据库连接",
                    "max_rules": 2,
                    "max_failures": 1,
                },
            },
        }
    )
    assert "result" in resp_call
    content_list = resp_call["result"]["content"]
    assert len(content_list) > 0
    res = json.loads(content_list[0]["text"])
    assert "task" in res
    assert "summary" in res
    assert "rules" in res
    assert "failures" in res
    assert "probes" in res


def test_middleware_x_task_header_injection(client_and_db):
    """验证 ASGI CognitiveMiddleware 在携带 X-Task 请求头时自动附加 X-Cognitive-Context。"""
    import urllib.parse

    client, _ = client_and_db

    task_encoded = urllib.parse.quote("修改配置并执行数据库操作")
    resp = client.get("/health", headers={"X-Task": task_encoded})
    assert resp.status_code == 200

    # 检查响应头中是否存在 X-Cognitive-Context
    cognitive_header = resp.headers.get("x-cognitive-context")
    assert cognitive_header is not None, "带 X-Task 的请求必须包含 X-Cognitive-Context 响应头"

    # Base64 解码检查内容
    decoded_text = base64.b64decode(cognitive_header.encode("ascii")).decode("utf-8")
    assert len(decoded_text) > 0
    assert "相关规则:" in decoded_text
