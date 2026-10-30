"""拾遗主流水线服务层、REST 路由、MCP 89 工具集成与认知上下文切面集成冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实启动 FastAPI TestClient、真实调用服务层与 MCP 处理器，直连 SQLite 不 mock；
- 覆盖 4 个独立接缝：
  1. Seam 1: 服务层 remedy_pipeline_service 真实调用与零召回自适应结果聚合；
  2. Seam 2: REST 路由 POST /shiyi/pipeline 端点鉴权与自适应补救返回；
  3. Seam 3: MCP 工具 shiyi_pipeline 请求处理、协议往返与 89 工具池双向对齐守护；
  4. Seam 4: 认知上下文 CognitiveContext 与主流水线自适应补救切面渲染。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.shiyi_service import remedy_pipeline_service
from lantai.storage import db


def _seed_memory(
    session,
    *,
    id: str,
    content: str,
    user_id: str = "user-alice",
    tenant_id: str | None = None,
    domain: str = "user",
    lane: str = "general",
    confidence: float = 0.9,
    status: str = "active",
) -> MemoryItem:
    t = utcnow()
    mem = MemoryItem(
        id=id,
        content=content,
        user_id=user_id,
        tenant_id=tenant_id,
        domain=domain,
        lane=lane,
        confidence=confidence,
        status=status,
        created_at=t,
        updated_at=t,
    )
    session.add(mem)
    return mem


class TestShiyiPipelineApiSmoke:
    """拾遗 Ticket 02 端到端集成冒烟测试。"""

    def test_seam1_service_layer_pipeline(self):
        """Seam 1: 服务层 remedy_pipeline_service 真实调用。"""
        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-service-shiyi-001",
                content="高可用Redis集群Sentinel哨兵节点选举法定人数与超时机制",
                user_id="user-svc",
            )
            session.commit()

        principal = Principal(user_id="user-svc")
        res = remedy_pipeline_service(
            query="Redis哨兵法定人数选举",
            top_k=3,
            allow_shiyi=True,
            principal=principal,
        )
        assert res["query"] == "Redis哨兵法定人数选举"
        assert res["total"] >= 1
        assert res["is_remedied"] is True
        assert len(res["results"]) >= 1
        assert res["results"][0]["memory"]["id"] == "mem-service-shiyi-001"

    def test_seam2_rest_route_pipeline(self):
        """Seam 2: REST 路由 POST /shiyi/pipeline 真实调用。"""
        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-route-shiyi-001",
                content="Docker容器虚拟化Namespace与Cgroups资源配额限制",
                user_id="default",
            )
            session.commit()

        client = TestClient(app)
        resp = client.post(
            "/shiyi/pipeline",
            json={
                "query": "Docker容器Cgroups资源配额",
                "top_k": 3,
                "allow_shiyi": True,
            },
            headers={"X-API-Key": "test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "Docker容器Cgroups资源配额"
        assert data["total"] >= 1
        assert data["is_remedied"] is True

    def test_seam3_mcp_pipeline_tool_and_89_parity(self):
        """Seam 3: MCP 工具 shiyi_pipeline 处理与 89+ 工具池 1:1 双向对齐。"""
        assert len(TOOLS) >= 89
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "shiyi_pipeline" in TOOLS
        assert "shiyi_pipeline" in TOOL_HANDLERS

        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-mcp-shiyi-001",
                content="Kubernetes Ingress-NGINX网关TLS证书自动化续签",
                user_id="default",
            )
            session.commit()

        resp = handle(
            {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {
                    "name": "shiyi_pipeline",
                    "arguments": {
                        "query": "Ingress网关TLS自动化续签",
                        "top_k": 2,
                    },
                },
            }
        )
        assert "result" in resp
        content_text = resp["result"]["content"][0]["text"]
        assert "is_remedied" in content_text
        assert "mem-mcp-shiyi-001" in content_text

    def test_seam4_cognitive_context_remedy_digest(self):
        """Seam 4: CognitiveContext 挂载拾遗自适应补偿切面并经樊篱防注入渲染。"""
        ctx = CognitiveContext(
            task="排查生产集群网络访问异常",
            shiyi_digest=[
                {
                    "id": "mem-digest-remedy-001",
                    "content": "集群Flannel网络CNI插件跨节点路由转发配置",
                    "remedy_tier": "fuzzy_tokens",
                    "confidence": 0.85,
                    "remedy_reason": "通过模糊关键词分词回溯补救",
                }
            ],
        )
        prompt = ctx.to_prompt()
        assert "## Compensated Recall & Remedied Memories (拾遗 · 1):" in prompt
        assert "[拾遗·R1 (fuzzy_tokens) [置信度: 0.85]]" in prompt
        assert "<memory_data" in prompt
        assert "集群Flannel网络CNI插件跨节点路由转发配置" in prompt
