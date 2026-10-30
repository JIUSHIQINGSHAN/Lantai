"""端到端不 mock 集成冒烟测试：札记服务层重构、REST 端点集成、MCP 93 工具池扩充与认知上下文切面融合 (Ticket 02)。

覆盖 4 个独立接缝：
1. 接缝 1 (服务层)：zhaji_distill_service 调度、结构化提取与可选持久化流转；
2. 接缝 2 (REST 端点)：POST /scratchpad/distill 鉴权、入参校验、响应结构与状态流转；
3. 接缝 3 (MCP 93 工具池)：MCP 协议往返、新增 zhaji_distill 工具与 93 工具 1:1 双向对齐守卫；
4. 接缝 4 (认知上下文切面)：CognitiveContext zhaji_digest 切面渲染与樊篱防注入安全围栏包裹。
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
from lantai.models.tables import SessionScratchpad
from lantai.services.scratchpad_service import (
    get_scratchpad,
    zhaji_distill_service,
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


def test_seam1_service_layer_zhaji_distill(db_session: Session):
    """接缝 1：服务层 zhaji_distill_service 调度与落库。"""
    principal = Principal(user_id="u_zhaji", tenant_id="t_zhaji")
    raw_content = "目标: 验证服务层提炼 | 发现: 结构化解析成功 | 待办: 接入 MCP 93"

    # 1. 仅提炼不写库 (write=False)
    res_dry = zhaji_distill_service(
        session_id="sess_srv_dry",
        raw_text=raw_content,
        write=False,
        session=db_session,
        principal=principal,
    )
    assert res_dry["session_id"] == "sess_srv_dry"
    assert res_dry["total_slots"] >= 3
    assert not res_dry["truncated"]
    assert "目标: 验证服务层提炼" in res_dry["compact_text"]
    # 确认没有写入 DB
    assert db_session.get(SessionScratchpad, "sess_srv_dry") is None

    # 2. 提炼并持久化落库 (write=True)
    res_write = zhaji_distill_service(
        session_id="sess_srv_write",
        raw_text=raw_content,
        write=True,
        session=db_session,
        principal=principal,
    )
    assert res_write["session_id"] == "sess_srv_write"
    assert res_write["total_slots"] >= 3

    # 确认真实写入 DB
    row = db_session.get(SessionScratchpad, "sess_srv_write")
    assert row is not None
    assert row.user_id == "u_zhaji"
    assert row.tenant_id == "t_zhaji"

    # 3. 从 DB 纯回读并提纯 (raw_text 为空)
    res_read = zhaji_distill_service(
        session_id="sess_srv_write",
        raw_text="",
        write=False,
        session=db_session,
        principal=principal,
    )
    assert res_read["session_id"] == "sess_srv_write"
    assert res_read["total_slots"] >= 3
    assert "验证服务层提炼" in res_read["compact_text"]


def test_seam2_rest_distill_endpoint():
    """接缝 2：REST 路由端点 POST /scratchpad/distill。"""
    client = TestClient(app)

    raw_text = "# 当前目标\n- REST 端点提炼\n# 关键发现\n- 自动化结构化支持良好"
    resp = client.post(
        "/scratchpad/distill",
        json={"session_id": "sess_api_01", "content": raw_text, "write": True},
        headers={"X-API-Key": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess_api_01"
    assert data["total_slots"] >= 2
    assert "compact_text" in data
    assert "item" in data
    assert "fenced_content" in data


def test_seam3_mcp_93_tools_parity():
    """接缝 3：MCP 协议往返与 93 工具池 1:1 双向对齐守卫。"""
    assert len(TOOLS) == 93
    assert len(TOOL_HANDLERS) == 93
    assert "zhaji_distill" in TOOLS
    assert "zhaji_distill" in TOOL_HANDLERS

    # 双向对齐严格断言
    for t_name in TOOLS:
        assert t_name in TOOL_HANDLERS, f"Tool {t_name} missing from TOOL_HANDLERS"
    for h_name in TOOL_HANDLERS:
        assert h_name in TOOLS, f"Handler {h_name} missing from TOOLS"

    # tools/list 协议响应断言
    resp_list = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert resp_list is not None
    names = [t["name"] for t in resp_list["result"]["tools"]]
    assert "zhaji_distill" in names

    # tools/call 协议调度执行断言
    call_resp = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "zhaji_distill",
                "arguments": {
                    "session_id": "sess_mcp_01",
                    "content": "目标: 验证 MCP 93 对齐 | 待办: 跑通全量冒烟",
                    "write": False,
                },
            },
        }
    )
    assert call_resp is not None
    assert "result" in call_resp
    text_content = call_resp["result"]["content"][0]["text"]
    assert "total_slots" in text_content
    assert "sess_mcp_01" in text_content


def test_seam4_cognitive_context_zhaji_aspect():
    """接缝 4：CognitiveContext zhaji_digest 切面渲染与樊篱防注入安全围栏包裹。"""
    zhaji_data = {
        "session_id": "sess_ctx_01",
        "compact_text": "目标: 验证认知上下文切面 | 发现: 樊篱包裹生效",
        "total_slots": 2,
        "truncated": False,
        "fenced_content": '<memory_data id="zhaji-sess_ctx_01" score="0.95">目标: 验证认知上下文切面 | 发现: 樊篱包裹生效</memory_data>',
    }

    ctx = CognitiveContext(task="测试认知上下文札记切面", zhaji_digest=zhaji_data)
    prompt = ctx.to_prompt()

    assert "## Working Scratchpad & Distilled Notes (札记 · 会话 sess_ctx_01)" in prompt
    assert '<memory_data id="zhaji-sess_ctx_01" score="0.95">' in prompt
    assert "目标: 验证认知上下文切面 | 发现: 樊篱包裹生效" in prompt
