"""拾遗（Shiyi）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 remedy_recall_service 纯净调用验证；
  2. REST 路由端点 POST /shiyi/remedy 往返验证；
  3. MCP 协议工具 shiyi_remedy 往返解析与 82 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 shiyi_digest 补救切面樊篱包裹验证。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.shiyi_service import remedy_recall_service
from lantai.storage import db


def _seed_test_memory() -> MemoryItem:
    t = utcnow()
    with db.get_session() as s:
        mem = MemoryItem(
            id=f"mem-shiyi-api-{t.timestamp()}",
            content="生产环境网关 Kong 插件严格限流配置为每秒 500 次请求",
            confidence=0.95,
            domain="user",
            lane="rule",
            status="active",
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        s.add(mem)
        s.commit()
        s.refresh(mem)
        return mem


class TestShiyiApiSmoke:
    """Shiyi Ticket 02 冒烟测试集。"""

    def test_seam1_shiyi_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        mem = _seed_test_memory()
        p = Principal(user_id="default")

        res = remedy_recall_service(
            query="Kong 限流 500",
            domain="user",
            limit=5,
            principal=p,
        )
        assert res["is_remedied"] is True
        assert len(res["remedied_items"]) >= 1
        assert any(item["id"] == mem.id for item in res["remedied_items"])

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /shiyi/remedy。"""
        mem = _seed_test_memory()
        client = TestClient(app)

        resp = client.post(
            "/shiyi/remedy",
            json={"query": "网关 Kong 限流配置", "domain": "all", "limit": 3},
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_remedied"] is True
        assert len(data["remedied_items"]) >= 1
        assert any(item["id"] == mem.id for item in data["remedied_items"])

    def test_seam3_mcp_82_tools_parity(self):
        """Seam 3: MCP 协议工具 shiyi_remedy 往返解析与 82+ 工具池 1:1 双向对齐守卫。"""
        mem = _seed_test_memory()

        # 82+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 82
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "shiyi_remedy" in TOOLS
        assert "shiyi_remedy" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "shiyi_remedy" in tool_names

        # tools/call RPC
        req_call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "shiyi_remedy",
                "arguments": {"query": "Kong 每秒 500 请求", "user_id": "default"},
            },
        }
        res = handle(req_call)
        assert res is not None and "result" in res
        data = json.loads(res["result"]["content"][0]["text"])
        assert data["is_remedied"] is True
        assert any(item["id"] == mem.id for item in data["remedied_items"])

    def test_seam4_cognitive_context_shiyi_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 shiyi_digest 补救切面樊篱包裹验证。"""
        items = [
            {
                "id": "mem-rem-001",
                "content": "Redis 哨兵模式高可用配置",
                "remedy_tier": "fuzzy_tokens",
                "confidence": 0.82,
                "remedy_reason": "长尾模糊匹配命中",
            }
        ]
        ctx = CognitiveContext(task="排查缓存集群架构", shiyi_digest=items)
        prompt = ctx.to_prompt()

        assert "## Compensated Recall & Remedied Memories (拾遗 · 1):" in prompt
        assert "[拾遗·R1 (fuzzy_tokens) [置信度: 0.82]]" in prompt
        assert '<memory_data id="mem-rem-001"' in prompt
        assert "Redis 哨兵模式高可用配置" in prompt
        assert "长尾模糊匹配命中" in prompt
