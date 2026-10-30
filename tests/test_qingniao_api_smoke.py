"""青鸟（Qingniao）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与信函投递过程；
- 覆盖 4 个公共接缝：
  1. 服务层 dispatch/fetch/ack 纯净调用验证；
  2. REST 路由端点 POST /qingniao/dispatch、GET /qingniao/inbox 与 POST /qingniao/ack 往返验证；
  3. MCP 协议工具 qingniao_messenger 往返解析与 88 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 qingniao_digest 观测切面樊篱包裹验证。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.cognition.qingniao import _QINGNIAO_MAILBOX
from lantai.core.auth import Principal
from lantai.services.qingniao_service import (
    acknowledge_letter_service,
    dispatch_letter_service,
    fetch_inbox_service,
)


@pytest.fixture(autouse=True)
def clean_mailbox():
    _QINGNIAO_MAILBOX.clear()
    yield
    _QINGNIAO_MAILBOX.clear()


class TestQingniaoApiSmoke:
    """Qingniao Ticket 02 冒烟测试集。"""

    def test_seam1_qingniao_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        p = Principal(user_id="default")
        # 1. dispatch_letter_service
        dispatched = dispatch_letter_service(
            sender_agent_id="agent_source",
            receiver_agent_id="agent_dest",
            subject="协同研发公文",
            content="请尽快推进青鸟信使服务集成。",
            priority="high",
            namespace="svc_test",
            principal=p,
        )
        assert dispatched["dispatch"]["is_dispatched"] is True
        letter = dispatched["letter"]
        lid = letter["letter_id"]
        assert "<memory_data" in letter["fenced_content"]

        # 2. fetch_inbox_service
        letters = fetch_inbox_service(
            receiver_agent_id="agent_dest",
            namespace="svc_test",
            principal=p,
        )
        assert len(letters) == 1
        assert letters[0]["letter_id"] == lid

        # 3. acknowledge_letter_service
        ack = acknowledge_letter_service(
            letter_id=lid,
            receiver_agent_id="agent_dest",
            action="ack",
            namespace="svc_test",
            principal=p,
        )
        assert ack["status"] == "acknowledged"

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /qingniao/dispatch、GET /qingniao/inbox 与 POST /qingniao/ack。"""
        client = TestClient(app)

        # 1. POST /qingniao/dispatch
        resp1 = client.post(
            "/qingniao/dispatch",
            json={
                "sender_agent_id": "sender_01",
                "receiver_agent_id": "receiver_01",
                "subject": "版本发布协调",
                "content": "v0.54.0 青鸟窗口正式开启。",
                "priority": "normal",
                "namespace": "api_test",
            },
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["dispatch"]["is_dispatched"] is True
        lid = d1["letter"]["letter_id"]

        # 2. GET /qingniao/inbox
        resp2 = client.get(
            "/qingniao/inbox",
            params={
                "receiver_agent_id": "receiver_01",
                "namespace": "api_test",
            },
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert len(d2) == 1
        assert d2[0]["letter_id"] == lid

        # 3. POST /qingniao/ack
        resp3 = client.post(
            "/qingniao/ack",
            json={
                "letter_id": lid,
                "receiver_agent_id": "receiver_01",
                "action": "ack",
                "namespace": "api_test",
            },
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["status"] == "acknowledged"

    def test_seam3_mcp_88_tools_parity(self):
        """Seam 3: MCP 协议工具 qingniao_messenger 往返解析与 88 工具池 1:1 双向对齐守卫。"""
        assert len(TOOLS) == 88
        assert len(TOOL_HANDLERS) == 88
        assert "qingniao_messenger" in TOOLS
        assert "qingniao_messenger" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "qingniao_messenger" in tool_names

        # tools/call RPC (派发信件)
        req_dispatch = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "qingniao_messenger",
                "arguments": {
                    "sender_agent_id": "agent_alpha",
                    "receiver_agent_id": "agent_omega",
                    "subject": "多智能体任务通报",
                    "content": "请协调记忆星图与法门流水线。",
                    "priority": "high",
                    "namespace": "mcp_test",
                    "user_id": "default",
                },
            },
        }
        res_d = handle(req_dispatch)
        assert res_d is not None and "result" in res_d
        data_d = json.loads(res_d["result"]["content"][0]["text"])
        assert data_d["dispatch"]["is_dispatched"] is True
        lid = data_d["letter"]["letter_id"]

        # tools/call RPC (查收信箱)
        req_inbox = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "qingniao_messenger",
                "arguments": {
                    "receiver_agent_id": "agent_omega",
                    "namespace": "mcp_test",
                    "user_id": "default",
                },
            },
        }
        res_i = handle(req_inbox)
        assert res_i is not None and "result" in res_i
        data_i = json.loads(res_i["result"]["content"][0]["text"])
        assert data_i["total"] == 1
        assert data_i["letters"][0]["letter_id"] == lid

        # tools/call RPC (确认签收)
        req_ack = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "qingniao_messenger",
                "arguments": {
                    "action": "ack",
                    "letter_id": lid,
                    "receiver_agent_id": "agent_omega",
                    "namespace": "mcp_test",
                    "user_id": "default",
                },
            },
        }
        res_a = handle(req_ack)
        assert res_a is not None and "result" in res_a
        data_a = json.loads(res_a["result"]["content"][0]["text"])
        assert data_a["status"] == "acknowledged"

    def test_seam4_cognitive_context_qingniao_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 qingniao_digest 观测切面樊篱包裹验证。"""
        ctx = CognitiveContext(task="测试青鸟信使切面渲染")
        ctx.qingniao_digest = [
            {
                "letter_id": "qnl_01J0001",
                "sender_agent_id": "agent_coordinator",
                "subject": "多体时序对齐通知",
                "content": "各智能体请同步起居注与起复记录。",
                "priority": "high",
            },
            {
                "letter_id": "qnl_01J0002",
                "sender_agent_id": "agent_worker",
                "subject": "日常心跳广播",
                "content": "工作节点一切运转正常。",
                "priority": "normal",
            },
        ]
        prompt = ctx.to_prompt()
        assert "## Multi-Agent Letters & Messenger (青鸟 · 2):" in prompt
        assert "[青鸟·M1 (high) [发信方: agent_coordinator] [主题: 多体时序对齐通知]]" in prompt
        assert "[青鸟·M2 (normal) [发信方: agent_worker] [主题: 日常心跳广播]]" in prompt
        assert '<memory_data id="qingniao-qnl_01J0001" score="1.0">' in prompt
        assert '<memory_data id="qingniao-qnl_01J0002" score="1.0">' in prompt
        assert "各智能体请同步起居注与起复记录" in prompt
