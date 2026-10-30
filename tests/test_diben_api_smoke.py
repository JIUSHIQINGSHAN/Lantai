"""底本（Diben）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 synthesize_anchor_service、analyze_drift_service 与 get_active_anchor_service 纯净调用验证；
  2. REST 路由端点 POST /diben/anchor、POST /diben/drift 与 GET /diben/latest 往返验证；
  3. MCP 协议工具 diben_anchor 往返解析与 85 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 diben_digest 定本切面樊篱包裹验证。
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
from lantai.models.tables import SessionCheckpoint
from lantai.services.diben_service import (
    analyze_drift_service,
    get_active_anchor_service,
    synthesize_anchor_service,
)
from lantai.storage import db


def _seed_test_diben_fixture(session_id: str = "sess-api-diben-01") -> None:
    t = utcnow()
    with db.get_session() as s:
        blocks = {
            "cp_active_intent": "迁移生产 Elasticsearch 到 OpenSearch 集群",
            "cp_next_action": "执行快照与索引恢复验证",
            "cp_current_work": "配置 S3 存储桶与仓库插件",
            "cp_key_decisions": "采用单向平滑迁移，保留旧索引只读 7 天",
            "cp_open_notes": "比对两边聚类搜索得分与延迟分布",
        }
        for k, v in blocks.items():
            cp = SessionCheckpoint(
                session_id=session_id,
                tenant_id=None,
                user_id="default",
                block_key=k,
                content=v,
                created_at=t,
            )
            s.add(cp)
        s.commit()


class TestDibenApiSmoke:
    """Diben Ticket 02 冒烟测试集。"""

    def test_seam1_diben_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        _seed_test_diben_fixture(session_id="sess-svc-test")
        p = Principal(user_id="default")

        # 1. get_active_anchor_service
        anchor = get_active_anchor_service(session_id="sess-svc-test", principal=p)
        assert anchor is not None
        assert anchor["active_intent"] == "迁移生产 Elasticsearch 到 OpenSearch 集群"

        # 2. analyze_drift_service
        drift = analyze_drift_service(
            query="OpenSearch 集群快照配置参数",
            session_id="sess-svc-test",
            principal=p,
        )
        assert drift["is_continuation"] is True
        assert drift["drift_level"] in ("none", "slight")

        # 3. synthesize_anchor_service
        new_anchor = synthesize_anchor_service(
            session_id="sess-svc-new",
            blocks={
                "cp_active_intent": "配置 RabbitMQ 镜像队列高可用策略",
                "cp_next_action": "设置 ha-mode 为 exactly",
            },
            principal=p,
        )
        assert new_anchor["session_id"] == "sess-svc-new"
        assert new_anchor["active_intent"] == "配置 RabbitMQ 镜像队列高可用策略"

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /diben/anchor、POST /diben/drift 与 GET /diben/latest。"""
        _seed_test_diben_fixture(session_id="sess-rest-diben")
        client = TestClient(app)

        # 1. POST /diben/anchor
        resp1 = client.post(
            "/diben/anchor",
            json={
                "session_id": "sess-rest-created",
                "blocks": {
                    "cp_active_intent": "升级 Nginx Ingress Controller 到 1.9",
                    "cp_next_action": "更新 Helm values.yaml",
                },
            },
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["session_id"] == "sess-rest-created"

        # 2. POST /diben/drift
        resp2 = client.post(
            "/diben/drift",
            json={
                "query": "继续更新 Helm 依赖包",
                "session_id": "sess-rest-created",
            },
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["is_continuation"] is True

        # 3. GET /diben/latest
        resp3 = client.get(
            "/diben/latest",
            params={"session_id": "sess-rest-created"},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["session_id"] == "sess-rest-created"

    def test_seam3_mcp_85_tools_parity(self):
        """Seam 3: MCP 协议工具 diben_anchor 往返解析与 85 工具池 1:1 双向对齐守卫。"""
        _seed_test_diben_fixture(session_id="sess-mcp-diben")

        # 85 工具总数与 1:1 对齐断言
        assert len(TOOLS) == 85
        assert len(TOOL_HANDLERS) == 85
        assert "diben_anchor" in TOOLS
        assert "diben_anchor" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "diben_anchor" in tool_names

        # tools/call RPC (漂移检测模式)
        req_call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "diben_anchor",
                "arguments": {
                    "query": "Elasticsearch 快照与数据迁移验证",
                    "session_id": "sess-mcp-diben",
                    "detect_drift": True,
                    "user_id": "default",
                },
            },
        }
        res = handle(req_call)
        assert res is not None and "result" in res
        data = json.loads(res["result"]["content"][0]["text"])
        assert data["is_continuation"] is True

        # tools/call RPC (写入定本模式)
        req_write = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "diben_anchor",
                "arguments": {
                    "session_id": "sess-mcp-new",
                    "blocks": {
                        "cp_active_intent": "实施 ClickHouse 分布式表分片优化",
                        "cp_next_action": "重构分布式 DDL 语法",
                    },
                    "user_id": "default",
                },
            },
        }
        res_w = handle(req_write)
        assert res_w is not None and "result" in res_w
        data_w = json.loads(res_w["result"]["content"][0]["text"])
        assert data_w["session_id"] == "sess-mcp-new"
        assert data_w["active_intent"] == "实施 ClickHouse 分布式表分片优化"

    def test_seam4_cognitive_context_diben_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 diben_digest 定本切面樊篱包裹验证。"""
        anchor_data = {
            "session_id": "sess-ctx-diben",
            "active_intent": "部署与调试 Apache APISIX 网关",
            "next_action": "加载 prometheus 与 jwt-auth 插件",
            "working_context": "调整 config.yaml 并重启容器",
            "key_decisions": ["网关层禁用冗余审计插件", "统一由外部 OAuth 服务发券"],
            "open_notes": ["验证突发限流测试脚本", "压测 QPS 指标"],
        }
        ctx = CognitiveContext(task="排查网关认证插件", diben_digest=anchor_data)
        prompt = ctx.to_prompt()

        assert "## Canonical Session Anchor (底本 · 会话 sess-ctx-diben):" in prompt
        assert "**在做 (Intent)**:" in prompt
        assert "**下一步 (Next Action)**:" in prompt
        assert "<memory_data" in prompt
        assert "部署与调试 Apache APISIX 网关" in prompt
        assert "加载 prometheus 与 jwt-auth 插件" in prompt
        assert "</memory_data>" in prompt
