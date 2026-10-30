"""拟议（Niyi）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 deliberate_proposal_service 与 audit_proposals_service 纯净调用验证；
  2. REST 路由端点 POST /niyi/deliberate 与 GET /niyi/audit 往返验证；
  3. MCP 协议工具 niyi_deliberate 往返解析与 84 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 niyi_digest 审议切面樊篱包裹验证。
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
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.services.niyi_service import (
    audit_proposals_service,
    deliberate_proposal_service,
)
from lantai.storage import db


def _seed_test_evolution_fixture() -> tuple[MemoryItem, MemoryProposal]:
    t = utcnow()
    with db.get_session() as s:
        mem = MemoryItem(
            id=f"mem-niyi-api-{t.timestamp()}",
            content="Kubernetes Ingress Controller 默认最大上传体积限制为 50MB",
            confidence=0.9,
            domain="user",
            lane="rule",
            status="active",
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        s.add(mem)

        edge = MemoryEdge(
            id=f"edge-niyi-api-{t.timestamp()}",
            source_memory_id=mem.id,
            target_memory_id=f"mem-target-{t.timestamp()}",
            relation="causes",
            confidence=0.88,
            reason="上传限流规则直接影响大文件上传服务可用性",
            user_id="default",
            created_at=t,
        )
        s.add(edge)

        prop = MemoryProposal(
            id=f"prop-niyi-api-{t.timestamp()}",
            proposal_type="update",
            target_memory_id=mem.id,
            reason="将限制调整为 100MB",
            proposed_patch={"content": "Kubernetes Ingress 上传体积上调至 100MB"},
            confidence=0.85,
            status="pending",
            user_id="default",
            created_at=t,
        )
        s.add(prop)
        s.commit()
        s.refresh(mem)
        s.refresh(prop)
        return mem, prop


class TestNiyiApiSmoke:
    """Niyi Ticket 02 冒烟测试集。"""

    def test_seam1_niyi_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        mem, _ = _seed_test_evolution_fixture()
        p = Principal(user_id="default")

        # 1. deliberate_proposal_service
        delib_res = deliberate_proposal_service(
            proposal_type="deprecate",
            patch_data={"status": "deprecated"},
            target_memory_id=mem.id,
            principal=p,
        )
        assert delib_res["target_memory_id"] == mem.id
        assert delib_res["risk_level"] == "high"
        assert delib_res["impacted_edges_count"] >= 1

        # 2. audit_proposals_service
        audit_res = audit_proposals_service(limit=10, principal=p)
        assert audit_res["total_pending"] >= 1
        assert "audit_summary" in audit_res

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /niyi/deliberate 与 GET /niyi/audit。"""
        mem, _ = _seed_test_evolution_fixture()
        client = TestClient(app)

        # 1. POST /niyi/deliberate
        resp1 = client.post(
            "/niyi/deliberate",
            json={
                "proposal_type": "update",
                "patch_data": {"content": "修改后的 Ingress 规则"},
                "target_memory_id": mem.id,
            },
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["target_memory_id"] == mem.id
        assert d1["risk_level"] in ("low", "medium", "high")

        # 2. GET /niyi/audit
        resp2 = client.get(
            "/niyi/audit",
            params={"limit": 5},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["total_pending"] >= 1

    def test_seam3_mcp_84_tools_parity(self):
        """Seam 3: MCP 协议工具 niyi_deliberate 往返解析与 84 工具池 1:1 双向对齐守卫。"""
        mem, _ = _seed_test_evolution_fixture()

        # 84+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 84
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "niyi_deliberate" in TOOLS
        assert "niyi_deliberate" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "niyi_deliberate" in tool_names

        # tools/call RPC (推演模式)
        req_call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "niyi_deliberate",
                "arguments": {
                    "proposal_type": "deprecate",
                    "target_memory_id": mem.id,
                    "user_id": "default",
                },
            },
        }
        res = handle(req_call)
        assert res is not None and "result" in res
        data = json.loads(res["result"]["content"][0]["text"])
        assert data["target_memory_id"] == mem.id
        assert data["risk_level"] == "high"

        # tools/call RPC (审计模式)
        req_call_audit = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "niyi_deliberate",
                "arguments": {
                    "audit": True,
                    "limit": 10,
                    "user_id": "default",
                },
            },
        }
        res_audit = handle(req_call_audit)
        assert res_audit is not None and "result" in res_audit
        data_audit = json.loads(res_audit["result"]["content"][0]["text"])
        assert data_audit["total_pending"] >= 1

    def test_seam4_cognitive_context_niyi_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 niyi_digest 审议切面樊篱包裹验证。"""
        items = [
            {
                "proposal_type": "deprecate",
                "target_memory_id": "mem-target-999",
                "risk_level": "high",
                "impacted_edges_count": 3,
                "deliberation_reason": "废弃该核心配置可能导致下游 3 条路由规则悬空",
            }
        ]
        ctx = CognitiveContext(task="演化安全评估", niyi_digest=items)
        prompt = ctx.to_prompt()

        assert "## Evolution Proposals & Deliberation (拟议 · 1):" in prompt
        assert "[拟议·P1 (deprecate·风险: high·拓扑边: 3) [目标: mem-target-999]]" in prompt
        assert "<memory_data" in prompt
        assert "废弃该核心配置可能导致下游 3 条路由规则悬空" in prompt
        assert "</memory_data>" in prompt
