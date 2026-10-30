"""法门（Famen）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 match_skills_service / get_skill_detail_service / list_skills_service 纯净调用；
  2. REST 路由端点 GET /famen/match、GET /famen/skills/{skill_id} 与 GET /famen/list；
  3. MCP 协议工具 famen_skill 往返解析与 80 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 famen_digest 技能切面樊篱包裹验证。
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
from lantai.models.tables import SkillCrystal
from lantai.services.famen_service import (
    get_skill_detail_service,
    list_skills_service,
    match_skills_service,
)
from lantai.storage import db


def _seed_crystal(name: str, trigger: str, procedure: str) -> SkillCrystal:
    with db.get_session() as s:
        sc = SkillCrystal(
            id=f"crys-api-{utcnow().timestamp()}",
            skill_name=name,
            trigger_rule=trigger,
            procedure=procedure,
            status="approved",
            user_id="default",
        )
        s.add(sc)
        s.commit()
        s.refresh(sc)
        return sc


class TestFamenApiSmoke:
    """Famen Ticket 02 冒烟测试集。"""

    def test_seam1_famen_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        c = _seed_crystal(
            "k8s-pod-restart-sop",
            "当 Pod 处于 CrashLoopBackOff 时",
            "- 步骤1: kubectl describe pod\n- 步骤2: kubectl logs --previous",
        )
        p = Principal(user_id="default")

        # 1. list_skills_service
        skills = list_skills_service(limit=10, principal=p)
        assert len(skills) >= 1
        assert any(s["id"] == c.id for s in skills)

        # 2. match_skills_service
        matched = match_skills_service(
            task="排查 Pod CrashLoopBackOff 重启失败", limit=5, principal=p
        )
        assert len(matched) >= 1
        assert matched[0]["id"] == c.id

        # 3. get_skill_detail_service
        detail = get_skill_detail_service(skill_id=c.id, principal=p)
        assert detail is not None
        assert detail["id"] == c.id
        assert "fenced_procedure" in detail

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 GET /famen/match, /skills/{id}, /list。"""
        c = _seed_crystal(
            "redis-cache-flush-sop",
            "当 Redis 缓存击穿需要按前缀清理时",
            "- 步骤1: redis-cli --scan --pattern\n- 步骤2: redis-cli unlink",
        )
        client = TestClient(app)

        # 1. GET /famen/match
        resp1 = client.get(
            "/famen/match",
            params={"task": "清理 Redis 缓存击穿数据", "limit": 3},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["count"] >= 1
        assert d1["skills"][0]["id"] == c.id

        # 2. GET /famen/skills/{skill_id}
        resp2 = client.get(
            f"/famen/skills/{c.id}",
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["id"] == c.id
        assert d2["name"] == "redis-cache-flush-sop"

        # 3. GET /famen/list
        resp3 = client.get(
            "/famen/list",
            params={"limit": 20},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["count"] >= 1

    def test_seam3_mcp_tool_and_80_parity(self):
        """Seam 3: MCP 工具 famen_skill 协议调用与 80 工具池 1:1 双向对齐守卫。"""
        # 工具池校验（80+ 工具双向一致）
        assert "famen_skill" in TOOLS
        assert "famen_skill" in TOOL_HANDLERS
        assert len(TOOLS) >= 80
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert set(TOOLS.keys()) == set(TOOL_HANDLERS.keys())

        # MCP 协议往返
        req_msg = {
            "jsonrpc": "2.0",
            "id": "req-fm-1",
            "method": "tools/call",
            "params": {
                "name": "famen_skill",
                "arguments": {
                    "task": "排查生产环境故障并执行重启 SOP",
                    "limit": 3,
                },
            },
        }
        resp = handle(req_msg)
        assert resp is not None
        assert "result" in resp
        content = resp["result"]["content"][0]["text"]
        data = json.loads(content)
        assert "task" in data
        assert "skills" in data

    def test_seam4_cognitive_context_skill_aspect(self):
        """Seam 4: CognitiveContext famen_digest 技能切面与樊篱防注入包裹。"""
        ctx = CognitiveContext(task="排查生产环境主从复制延迟")
        ctx.famen_digest = [
            {
                "name": "mysql-replication-delay-sop",
                "trigger_rule": "当 Seconds_Behind_Master > 300 时触发",
                "steps": [
                    "SHOW SLAVE STATUS\\G",
                    "检查 Relay Log 写入速率",
                    "临时跳过非关键事务或提升并行回放线程",
                ],
            }
        ]

        prompt = ctx.to_prompt()

        # 1. 标题切面呈现
        assert "## Applicable Procedural Skills & SOP (法门 · 1):" in prompt
        assert "mysql-replication-delay-sop" in prompt

        # 2. 樊篱（wrap_as_data）防注入隔离检查
        assert "<memory_data" in prompt
        assert "</memory_data>" in prompt
        assert "SHOW SLAVE STATUS" in prompt
        assert "临时跳过非关键事务" in prompt
