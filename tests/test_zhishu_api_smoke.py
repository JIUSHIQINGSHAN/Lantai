"""直书（Zhishu）API、服务层与 MCP 工具集成冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API 与 MCP，不 mock 核心计算逻辑；
- 覆盖 4 大公共接缝：
  1. zhishu_service 服务层接口解析与匹配校验；
  2. REST 调试端点 POST /parsing/zhishu/match；
  3. MCP 工具 zhishu_check 调用往返与返回格式；
  4. MCP 77 工具池 1:1 双向对齐守卫（TOOLS vs TOOL_HANDLERS）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.services.zhishu_service import match_zhishu_service


class TestZhishuApiSmoke:
    """Zhishu Ticket 02 冒烟测试集。"""

    def test_zhishu_service_seam(self):
        """Seam 1: 服务层 zhishu_service 纯净调用验证。"""
        # 命中正例
        r1 = match_zhishu_service("严禁未授权修改发布门禁")
        assert r1["matched"] is True
        assert r1["rule"] is not None
        assert r1["rule"]["lane"] == "rule"
        assert r1["rule"]["rail"] == "constraint"
        assert r1["rule"]["decay_class"] == "procedural"
        assert "未授权修改发布门禁" in r1["rule"]["constraints"][0]

        # 未命中负例（宁 miss 不脏写）
        r2 = match_zhishu_service("明天我们要不要去打球呢？")
        assert r2["matched"] is False
        assert r2["rule"] is None

    def test_zhishu_rest_endpoint_seam(self):
        """Seam 2: REST 路由端点 POST /parsing/zhishu/match。"""
        client = TestClient(app)

        # 1. 命中匹配
        resp1 = client.post(
            "/parsing/zhishu/match",
            json={"text": "配置 max_batch_size 为 64"},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["matched"] is True
        assert data1["rule"]["rail"] == "config"
        assert data1["rule"]["config_key"] == "max_batch_size"
        assert data1["rule"]["config_value"] == "64"

        # 2. 未命中匹配
        resp2 = client.post(
            "/parsing/zhishu/match",
            json={"text": "今天天气真不错啊"},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["matched"] is False
        assert data2["rule"] is None

        # 3. 参数校验异常
        resp3 = client.post(
            "/parsing/zhishu/match",
            json={"text": ""},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 422

    def test_zhishu_mcp_tools_seam(self):
        """Seam 3: MCP 协议工具 zhishu_check 往返调用。"""
        req_msg = {
            "jsonrpc": "2.0",
            "id": "req-zhishu-1",
            "method": "tools/call",
            "params": {
                "name": "zhishu_check",
                "arguments": {
                    "text": "务必遵循宁 miss 不脏写原则",
                },
            },
        }
        resp = handle(req_msg)
        assert resp is not None
        assert "result" in resp
        content_items = resp["result"]["content"]
        assert len(content_items) == 1
        res_data = json.loads(content_items[0]["text"])

        assert res_data["matched"] is True
        assert res_data["rule"]["rail"] == "constraint"
        assert res_data["rule"]["lane"] == "rule"
        assert "遵循宁 miss 不脏写原则" in res_data["rule"]["constraints"][0]

    def test_mcp_tools_parity_seam(self):
        """Seam 4: MCP 77 工具池与处理器 1:1 双向严苛守卫。"""
        assert "zhishu_check" in TOOLS
        assert "zhishu_check" in TOOL_HANDLERS
        assert len(TOOLS) == 77
        assert len(TOOL_HANDLERS) == 77
        assert set(TOOLS.keys()) == set(TOOL_HANDLERS.keys())
