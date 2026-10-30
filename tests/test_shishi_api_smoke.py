"""石室（Shishi）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 seal/verify/get 纯净调用验证；
  2. REST 路由端点 POST /shishi/seal、GET /shishi/verify 与 GET /shishi/blocks 往返验证；
  3. MCP 协议工具 shishi_vault 往返解析与 87 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 shishi_digest 观测切面樊篱包裹验证。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.services.shishi_service import (
    get_sealed_blocks_service,
    seal_core_memory_service,
    verify_shishi_integrity_service,
)


class TestShishiApiSmoke:
    """Shishi Ticket 02 冒烟测试集。"""

    def test_seam1_shishi_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        p = Principal(user_id="default")
        # 1. seal_core_memory_service
        sealed = seal_core_memory_service(
            block="identity",
            content="兰台高密记忆档案库，坚如磐石。",
            is_immutable=False,
            namespace="svc_test",
            principal=p,
        )
        assert sealed["block_key"] == "identity"
        assert sealed["version"] == 1
        assert "<memory_data" in sealed["fenced_content"]

        # 2. verify_shishi_integrity_service
        v = verify_shishi_integrity_service(namespace="svc_test", principal=p)
        assert v["is_valid"] is True
        assert v["verified_blocks"] >= 1

        # 3. get_sealed_blocks_service
        blocks = get_sealed_blocks_service(namespace="svc_test", principal=p)
        assert len(blocks) >= 1
        assert blocks[0]["block_key"] == "identity"

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /shishi/seal、GET /shishi/verify 与 GET /shishi/blocks。"""
        client = TestClient(app)

        # 1. POST /shishi/seal
        resp1 = client.post(
            "/shishi/seal",
            json={
                "block": "policy",
                "content": "石室铁律：核心资产不可非法越权覆写。",
                "is_immutable": False,
                "namespace": "api_test",
            },
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["block_key"] == "policy"
        assert d1["version"] == 1

        # 2. GET /shishi/verify
        resp2 = client.get(
            "/shishi/verify",
            params={"namespace": "api_test"},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["is_valid"] is True

        # 3. GET /shishi/blocks
        resp3 = client.get(
            "/shishi/blocks",
            params={"namespace": "api_test"},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert len(d3) >= 1
        assert d3[0]["block_key"] == "policy"

    def test_seam3_mcp_87_tools_parity(self):
        """Seam 3: MCP 协议工具 shishi_vault 往返解析与工具池 1:1 双向对齐守卫。"""
        assert len(TOOLS) >= 87
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "shishi_vault" in TOOLS
        assert "shishi_vault" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "shishi_vault" in tool_names

        # tools/call RPC (封存并读取)
        req_seal = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "shishi_vault",
                "arguments": {
                    "block": "task",
                    "content": "金匮核心任务：守护认知硬核不移。",
                    "is_immutable": False,
                    "namespace": "mcp_test",
                    "user_id": "default",
                },
            },
        }
        res_s = handle(req_seal)
        assert res_s is not None and "result" in res_s
        data_s = json.loads(res_s["result"]["content"][0]["text"])
        assert data_s["block_key"] == "task"

        # tools/call RPC (验真)
        req_verify = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "shishi_vault",
                "arguments": {
                    "verify": True,
                    "namespace": "mcp_test",
                    "user_id": "default",
                },
            },
        }
        res_v = handle(req_verify)
        assert res_v is not None and "result" in res_v
        data_v = json.loads(res_v["result"]["content"][0]["text"])
        assert data_v["is_valid"] is True

    def test_seam4_cognitive_context_shishi_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 shishi_digest 观测切面樊篱包裹验证。"""
        ctx = CognitiveContext(task="测试石室切面渲染")
        ctx.shishi_digest = [
            {
                "block_key": "identity",
                "content": "兰台首席档案馆官署档案守护者",
                "is_immutable": True,
                "version": 3,
            },
            {
                "block_key": "policy",
                "content": "严禁私自覆写核心记忆硬核",
                "is_immutable": False,
                "version": 1,
            },
        ]
        prompt = ctx.to_prompt()
        assert "## Core Memory Vault (石室 · 2):" in prompt
        assert "[石室·V1 (identity·v3) [金匮锁定]]" in prompt
        assert "[石室·V2 (policy·v1) [可调整]]" in prompt
        assert '<memory_data id="shishi-identity" score="1.0">' in prompt
        assert '<memory_data id="shishi-policy" score="1.0">' in prompt
        assert "严禁私自覆写核心记忆硬核" in prompt
