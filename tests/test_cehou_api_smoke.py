"""测候（Cehou）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 create/inspect/scan/record 纯净调用验证；
  2. REST 路由端点 POST /cehou/windows、POST /cehou/inspect 与 GET /cehou/windows 往返验证；
  3. MCP 协议工具 cehou_observe 往返解析与 86 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 cehou_digest 观测切面樊篱包裹验证。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.services.cehou_service import (
    create_cehou_window_service,
    inspect_cehou_window_service,
    record_cehou_sample_service,
    scan_cehou_windows_service,
)


class TestCehouApiSmoke:
    """Cehou Ticket 02 冒烟测试集。"""

    def test_seam1_cehou_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        p = Principal(user_id="default")
        w_id = "win-svc-api-test-01"

        # 1. create_cehou_window_service
        w = create_cehou_window_service(
            window_id=w_id,
            target_type="rule",
            target_id="rule-gate-timeout",
            required_days=7,
            min_samples=5,
            principal=p,
        )
        assert w["window_id"] == w_id
        assert w["state"] == "observing"

        # 2. record_cehou_sample_service
        rec = record_cehou_sample_service(
            window_id=w_id,
            success=True,
            latency_ms=25.0,
            score_delta=0.08,
            principal=p,
        )
        assert rec["success"] is True
        assert rec["sample_count"] == 1

        # 3. inspect_cehou_window_service
        ins = inspect_cehou_window_service(window_id=w_id, principal=p)
        assert ins is not None
        assert ins["sample_count"] == 1
        assert ins["success_rate"] == 1.0

        # 4. scan_cehou_windows_service
        scan = scan_cehou_windows_service(principal=p)
        assert scan["total_windows"] >= 1

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /cehou/windows、POST /cehou/inspect 与 GET /cehou/windows。"""
        client = TestClient(app)
        w_id = "win-rest-test-01"

        # 1. POST /cehou/windows
        resp1 = client.post(
            "/cehou/windows",
            json={
                "window_id": w_id,
                "target_type": "param",
                "target_id": "param-bm25-k1",
                "required_days": 5,
                "min_samples": 8,
            },
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["window_id"] == w_id

        # 2. POST /cehou/sample
        resp_s = client.post(
            "/cehou/sample",
            json={
                "window_id": w_id,
                "success": True,
                "latency_ms": 32.5,
                "score_delta": 0.1,
            },
            headers={"X-API-Key": "test"},
        )
        assert resp_s.status_code == 200

        # 3. POST /cehou/inspect
        resp2 = client.post(
            "/cehou/inspect",
            json={"window_id": w_id, "mock_days_observed": 3},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["window_id"] == w_id
        assert d2["days_observed"] == 3

        # 4. GET /cehou/windows
        resp3 = client.get(
            "/cehou/windows",
            params={"target_type": "param"},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["total_windows"] >= 1

    def test_seam3_mcp_86_tools_parity(self):
        """Seam 3: MCP 协议工具 cehou_observe 往返解析与 86 工具池 1:1 双向对齐守卫。"""
        # 86+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 86
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "cehou_observe" in TOOLS
        assert "cehou_observe" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "cehou_observe" in tool_names

        # tools/call RPC (创建视窗)
        w_mcp = "win-mcp-test-01"
        req_create = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "cehou_observe",
                "arguments": {
                    "create": True,
                    "window_id": w_mcp,
                    "target_type": "proposal",
                    "target_id": "prop-eval-99",
                    "required_days": 7,
                    "min_samples": 10,
                    "user_id": "default",
                },
            },
        }
        res_c = handle(req_create)
        assert res_c is not None and "result" in res_c
        data_c = json.loads(res_c["result"]["content"][0]["text"])
        assert data_c["window_id"] == w_mcp

        # tools/call RPC (样本上报)
        req_sample = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "cehou_observe",
                "arguments": {
                    "record_sample": True,
                    "window_id": w_mcp,
                    "success": True,
                    "latency_ms": 15.0,
                    "user_id": "default",
                },
            },
        }
        res_s = handle(req_sample)
        assert res_s is not None and "result" in res_s
        data_s = json.loads(res_s["result"]["content"][0]["text"])
        assert data_s["success"] is True

        # tools/call RPC (巡检视窗)
        req_inspect = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "cehou_observe",
                "arguments": {
                    "window_id": w_mcp,
                    "user_id": "default",
                },
            },
        }
        res_i = handle(req_inspect)
        assert res_i is not None and "result" in res_i
        data_i = json.loads(res_i["result"]["content"][0]["text"])
        assert data_i["window_id"] == w_mcp
        assert data_i["sample_count"] == 1

    def test_seam4_cognitive_context_cehou_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 cehou_digest 观测切面樊篱包裹验证。"""
        windows = [
            {
                "window_id": "win-ctx-cehou-01",
                "state": "observing",
                "success_rate": 0.95,
                "observation_summary": "视窗 win-ctx-cehou-01 (param:k1) 观测中，样本数: 15/10，胜率: 95.0%",
            }
        ]
        ctx = CognitiveContext(task="观察期状态巡检", cehou_digest=windows)
        prompt = ctx.to_prompt()

        assert "## Shadow Observation & Graduation (测候 · 1):" in prompt
        assert "[测候·W1 (observing·胜率: 95.0%) [视窗: win-ctx-cehou-01]]" in prompt
        assert "<memory_data" in prompt
        assert "视窗 win-ctx-cehou-01 (param:k1) 观测中" in prompt
        assert "</memory_data>" in prompt
