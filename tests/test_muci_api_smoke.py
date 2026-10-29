"""目次（Muci）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 get_catalog_service 聚合与参数透传；
  2. REST 路由端点 GET /catalog/overview 与 GET /catalog/domains/{domain}；
  3. MCP 协议工具 muci_catalog 往返解析与 78 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 catalog_digest 导览切面樊篱包裹验证。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem
from lantai.services.muci_service import get_catalog_service
from lantai.storage import db


def _seed_db(
    content: str, domain: str = "user", lane: str = "fact", user_id: str = "test-user"
) -> MemoryItem:
    with db.get_session() as s:
        mem = MemoryItem(
            id=f"mem-{domain}-{lane}",
            content=content,
            domain=domain,
            lane=lane,
            user_id=user_id,
            status="active",
            importance=0.9,
            tags=["demo", "test"],
        )
        s.add(mem)
        s.commit()
        s.refresh(mem)
        return mem


class TestMuciApiSmoke:
    """Muci Ticket 02 冒烟测试集。"""

    def test_seam1_muci_service(self):
        """Seam 1: 服务层 get_catalog_service 纯净调用验证。"""
        _seed_db("用户现居上海市浦东新区", domain="user", lane="fact")
        p = Principal(user_id="test-user")
        cat = get_catalog_service(principal=p, domain="user")
        assert cat["total_memories"] >= 1
        assert "user" in cat["domains"]
        assert len(cat["sections"]) >= 1

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 GET /catalog/overview 与 GET /catalog/domains/{domain}。"""
        client = TestClient(app)

        # 1. GET /catalog/overview
        resp1 = client.get(
            "/catalog/overview",
            params={"detail_level": "compact"},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert "total_memories" in d1
        assert "sections" in d1

        # 2. GET /catalog/domains/{domain}
        resp2 = client.get(
            "/catalog/domains/user",
            params={"detail_level": "full", "max_entries": 2},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert "sections" in d2
        if d2["sections"]:
            assert d2["sections"][0]["domain"] == "user"

    def test_seam3_mcp_tool_and_parity(self):
        """Seam 3: MCP 工具 muci_catalog 协议调用与 78 工具池 1:1 双向对齐。"""
        # 工具池校验（78 工具双向一致）
        assert "muci_catalog" in TOOLS
        assert "muci_catalog" in TOOL_HANDLERS
        assert len(TOOLS) == 78
        assert len(TOOL_HANDLERS) == 78
        assert set(TOOLS.keys()) == set(TOOL_HANDLERS.keys())

        # MCP 协议往返
        req_msg = {
            "jsonrpc": "2.0",
            "id": "req-muci-1",
            "method": "tools/call",
            "params": {
                "name": "muci_catalog",
                "arguments": {
                    "domain": "user",
                    "detail_level": "compact",
                },
            },
        }
        resp = handle(req_msg)
        assert resp is not None
        assert "result" in resp
        content_items = resp["result"]["content"]
        assert len(content_items) == 1
        res_data = json.loads(content_items[0]["text"])
        assert "total_memories" in res_data
        assert "sections" in res_data

    def test_seam4_cognitive_context_catalog_digest(self):
        """Seam 4: CognitiveContext 导览切面装配与樊篱防御渲染。"""
        fake_catalog = {
            "summary": "全域记忆知识概览：包含事实与规则",
            "sections": [
                {
                    "domain": "user",
                    "lane": "fact",
                    "count": 3,
                    "tags": ["python", "arch"],
                }
            ],
        }
        ctx = CognitiveContext(
            task="架构演化分析",
            catalog_digest=fake_catalog,
        )
        prompt = ctx.to_prompt()

        assert "## Memory Catalog & Knowledge Index" in prompt
        assert "全域记忆知识概览" in prompt
        assert "<memory_data" in prompt
        assert "</memory_data>" in prompt
        assert "user/fact: 3 items" in prompt
        assert "python" in prompt
