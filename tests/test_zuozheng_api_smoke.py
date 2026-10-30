"""佐证（Zuozheng）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 inspect_evidence_service / verify_claim_service 纯净调用；
  2. REST 路由端点 GET /zuozheng/inspect/{id} 与 POST /zuozheng/verify；
  3. MCP 协议工具 zuozheng_evidence 往返解析与 81 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 zuozheng_digest 证据切面樊篱包裹验证。
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
from lantai.models.tables import Evidence, MemoryEdge, MemoryItem
from lantai.services.zuozheng_service import (
    inspect_evidence_service,
    verify_claim_service,
)
from lantai.storage import db


def _seed_test_data() -> tuple[MemoryItem, MemoryItem, Evidence]:
    t = utcnow()
    with db.get_session() as s:
        target = MemoryItem(
            id=f"mem-target-{t.timestamp()}",
            content="生产环境已完成零停机平滑升级",
            confidence=0.95,
            status="active",
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        support = MemoryItem(
            id=f"mem-support-{t.timestamp()}",
            content="金丝雀发布阶段全量请求错误率为 0.00%",
            confidence=0.98,
            status="active",
            user_id="default",
            created_at=t,
            updated_at=t,
        )
        edge = MemoryEdge(
            id=f"edge-api-{t.timestamp()}",
            source_memory_id=support.id,
            target_memory_id=target.id,
            relation="supports",
            confidence=0.96,
            reason="金丝雀监控指标支撑升级平滑性",
            user_id="default",
            created_at=t,
        )
        ev = Evidence(
            id=f"ev-api-{t.timestamp()}",
            source_memory_id=target.id,
            content="Prometheus 报警看板无异常触发",
            evidence_type="monitor_metrics",
            reliability=0.99,
            user_id="default",
            created_at=t,
        )
        s.add(target)
        s.add(support)
        s.add(edge)
        s.add(ev)
        s.commit()
        s.refresh(target)
        s.refresh(support)
        s.refresh(ev)
        return target, support, ev


class TestZuozhengApiSmoke:
    """Zuozheng Ticket 02 冒烟测试集。"""

    def test_seam1_zuozheng_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        target, support, ev = _seed_test_data()
        p = Principal(user_id="default")

        # 1. inspect_evidence_service
        inspect_res = inspect_evidence_service(memory_id=target.id, max_depth=2, principal=p)
        assert inspect_res["target_memory_id"] == target.id
        assert inspect_res["total_nodes"] >= 2
        assert any(n["id"] == support.id for n in inspect_res["evidence_nodes"])

        # 2. verify_claim_service
        verify_res = verify_claim_service(claim="金丝雀发布错误率如何？", limit=5, principal=p)
        assert verify_res["verified"] is True
        assert any(n["id"] == support.id for n in verify_res["supporting_nodes"])

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 GET /zuozheng/inspect/{id} 与 POST /zuozheng/verify。"""
        target, support, ev = _seed_test_data()
        client = TestClient(app)

        # 1. GET /zuozheng/inspect/{memory_id}
        resp1 = client.get(
            f"/zuozheng/inspect/{target.id}",
            params={"max_depth": 2},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["target_memory_id"] == target.id
        assert d1["total_nodes"] >= 2

        # 2. POST /zuozheng/verify
        resp2 = client.post(
            "/zuozheng/verify",
            json={"claim": "生产环境是否完成了平滑升级？", "limit": 3},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["verified"] is True
        assert len(d2["supporting_nodes"]) >= 1

    def test_seam3_mcp_81_tools_parity(self):
        """Seam 3: MCP 协议工具 zuozheng_evidence 往返解析与 81+ 工具池 1:1 双向对齐守卫。"""
        target, _, _ = _seed_test_data()

        # 81+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 81
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "zuozheng_evidence" in TOOLS
        assert "zuozheng_evidence" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "zuozheng_evidence" in tool_names

        # tools/call RPC - inspect mode
        req_call1 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "zuozheng_evidence",
                "arguments": {"memory_id": target.id, "user_id": "default"},
            },
        }
        res1 = handle(req_call1)
        assert res1 is not None and "result" in res1
        data1 = json.loads(res1["result"]["content"][0]["text"])
        assert data1["target_memory_id"] == target.id

        # tools/call RPC - verify mode
        req_call2 = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "zuozheng_evidence",
                "arguments": {"claim": "金丝雀发布监控", "user_id": "default"},
            },
        }
        res2 = handle(req_call2)
        assert res2 is not None and "result" in res2
        data2 = json.loads(res2["result"]["content"][0]["text"])
        assert data2["verified"] is True

    def test_seam4_cognitive_context_zuozheng_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 zuozheng_digest 证据切面樊篱包裹验证。"""
        items = [
            {
                "id": "mem-ev-001",
                "content": "单元测试通过率 100%",
                "support_direction": "supports",
                "confidence": 0.99,
                "relation_reason": "CI流水线通过",
            },
            {
                "id": "mem-ev-002",
                "content": "核心代码分支覆盖率 85%",
                "support_direction": "supports",
                "confidence": 0.92,
                "relation_reason": "代码覆盖率报告",
            },
        ]
        ctx = CognitiveContext(task="发布门禁检查", zuozheng_digest=items)
        prompt = ctx.to_prompt()

        assert "## Supporting Evidence & Proof Chain (佐证 · 2):" in prompt
        assert "[佐证·E1 (supports) [置信度: 0.99]]" in prompt
        assert '<memory_data id="mem-ev-001"' in prompt
        assert "单元测试通过率 100%" in prompt
        assert "CI流水线通过" in prompt
