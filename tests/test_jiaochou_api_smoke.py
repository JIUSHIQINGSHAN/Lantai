"""校雠（Jiaochou）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 collate_memories_service / scan_redundancy_service 纯净调用验证；
  2. REST 路由端点 POST /jiaochou/collate 与 GET /jiaochou/scan 往返验证；
  3. MCP 协议工具 jiaochou_collate 往返解析与 83 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 jiaochou_digest 补救切面樊篱包裹验证。
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
from lantai.services.jiaochou_service import (
    collate_memories_service,
    scan_redundancy_service,
)
from lantai.storage import db


def _seed_test_pair() -> tuple[MemoryItem, MemoryItem]:
    t = utcnow()
    with db.get_session() as s:
        m1 = MemoryItem(
            id=f"mem-jc-api-1-{t.timestamp()}",
            content="生产集群已开启两级本地与分布式缓存",
            confidence=0.92,
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        m2 = MemoryItem(
            id=f"mem-jc-api-2-{t.timestamp()}",
            content="生产环境集群配置了两级本地与分布式缓存架构",
            confidence=0.95,
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        s.add(m1)
        s.add(m2)
        s.commit()
        s.refresh(m1)
        s.refresh(m2)
        return m1, m2


class TestJiaochouApiSmoke:
    """Jiaochou Ticket 02 冒烟测试集。"""

    def test_seam1_jiaochou_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        m1, m2 = _seed_test_pair()
        p = Principal(user_id="default")

        # 1. collate_memories_service
        collate_res = collate_memories_service(
            text_a=m1.content,
            text_b=m2.content,
            memory_id_a=m1.id,
            memory_id_b=m2.id,
            principal=p,
        )
        assert collate_res["similarity"] > 0.5
        assert collate_res["action_suggestion"] in ("merge", "update")
        assert len(collate_res["overlap_tokens"]) >= 1

        # 2. scan_redundancy_service
        scan_res = scan_redundancy_service(limit=10, similarity_threshold=0.5, principal=p)
        assert scan_res["total_pairs"] >= 1
        assert any(
            {p["memory_id_a"], p["memory_id_b"]} == {m1.id, m2.id}
            for p in scan_res["redundant_pairs"]
        )

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /jiaochou/collate 与 GET /jiaochou/scan。"""
        m1, m2 = _seed_test_pair()
        client = TestClient(app)

        # 1. POST /jiaochou/collate
        resp1 = client.post(
            "/jiaochou/collate",
            json={"text_a": m1.content, "text_b": m2.content},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["similarity"] > 0.5
        assert d1["synthesized_proposal"] != ""

        # 2. GET /jiaochou/scan
        resp2 = client.get(
            "/jiaochou/scan",
            params={"limit": 5, "similarity_threshold": 0.5},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["total_pairs"] >= 1

    def test_seam3_mcp_83_tools_parity(self):
        """Seam 3: MCP 协议工具 jiaochou_collate 往返解析与 83 工具池 1:1 双向对齐守卫。"""
        m1, m2 = _seed_test_pair()

        # 83+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 83
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "jiaochou_collate" in TOOLS
        assert "jiaochou_collate" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "jiaochou_collate" in tool_names

        # tools/call RPC - collate mode
        req_call1 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "jiaochou_collate",
                "arguments": {
                    "text_a": m1.content,
                    "text_b": m2.content,
                    "user_id": "default",
                },
            },
        }
        res1 = handle(req_call1)
        assert res1 is not None and "result" in res1
        data1 = json.loads(res1["result"]["content"][0]["text"])
        assert data1["similarity"] > 0.5

        # tools/call RPC - scan mode
        req_call2 = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "jiaochou_collate",
                "arguments": {
                    "scan": True,
                    "similarity_threshold": 0.5,
                    "user_id": "default",
                },
            },
        }
        res2 = handle(req_call2)
        assert res2 is not None and "result" in res2
        data2 = json.loads(res2["result"]["content"][0]["text"])
        assert "redundant_pairs" in data2

    def test_seam4_cognitive_context_jiaochou_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 jiaochou_digest 校雠切面樊篱包裹验证。"""
        items = [
            {
                "synthesized_proposal": "统一将生产数据库端口设定为 5432",
                "action_suggestion": "merge",
                "similarity": 0.95,
                "divergence_summary": "表述同义合并",
            }
        ]
        ctx = CognitiveContext(task="数据库端口统一配置", jiaochou_digest=items)
        prompt = ctx.to_prompt()

        assert "## Semantic Alignment & Collation (校雠 · 1):" in prompt
        assert "[校雠·C1 (merge) [相似度: 0.95]]" in prompt
        assert "<memory_data" in prompt
        assert "统一将生产数据库端口设定为 5432" in prompt
        assert "表述同义合并" in prompt
