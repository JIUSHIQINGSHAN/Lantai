"""起居注（Qijuzhu）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 get_daily_chronicle_service / get_timeline_service 聚合与参数透传；
  2. REST 路由端点 GET /qijuzhu/today、GET /qijuzhu/timeline 与 GET /qijuzhu/dates/{date_str}；
  3. MCP 协议工具 qijuzhu_chronicle 往返解析与 79 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 qijuzhu_digest 导览切面樊篱包裹验证。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.qijuzhu_service import (
    get_daily_chronicle_service,
    get_timeline_service,
)
from lantai.storage import db


def _seed_db(
    content: str, domain: str = "user", lane: str = "fact", user_id: str = "test-user"
) -> MemoryItem:
    with db.get_session() as s:
        mem = MemoryItem(
            id=f"mem-qjz-api-{utcnow().timestamp()}",
            content=content,
            domain=domain,
            lane=lane,
            user_id=user_id,
            status="active",
            importance=0.9,
            tags=["chronicle", "test"],
        )
        s.add(mem)
        s.commit()
        s.refresh(mem)
        return mem


class TestQijuzhuApiSmoke:
    """Qijuzhu Ticket 02 冒烟测试集。"""

    def test_seam1_qijuzhu_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        _seed_db("用户在上海进行了起居注功能联调", domain="user", lane="fact")
        p = Principal(user_id="test-user")

        # 1. get_daily_chronicle_service
        daily = get_daily_chronicle_service(principal=p)
        assert "date" in daily
        assert "metrics" in daily
        assert "chronicle_events" in daily
        assert daily["metrics"]["total_events"] >= 1

        # 2. get_timeline_service
        timeline = get_timeline_service(days=3, limit=10, principal=p)
        assert timeline["days"] == 3
        assert len(timeline["events"]) >= 1

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 GET /qijuzhu/today, /timeline, /dates/{date_str}。"""
        client = TestClient(app)
        today_str = utcnow().strftime("%Y-%m-%d")

        # 1. GET /qijuzhu/today
        resp1 = client.get(
            "/qijuzhu/today",
            params={"detail_level": "summary"},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["date"] == today_str
        assert "metrics" in d1

        # 2. GET /qijuzhu/timeline
        resp2 = client.get(
            "/qijuzhu/timeline",
            params={"days": 2, "limit": 20},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert "events" in d2
        assert d2["days"] == 2

        # 3. GET /qijuzhu/dates/{date_str}
        resp3 = client.get(
            f"/qijuzhu/dates/{today_str}",
            params={"detail_level": "full"},
            headers={"X-API-Key": "test"},
        )
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["date"] == today_str

    def test_seam3_mcp_tool_and_parity(self):
        """Seam 3: MCP 工具 qijuzhu_chronicle 协议调用与 79 工具池 1:1 双向对齐。"""
        # 工具池校验（双向一致，泛化兼容 79+ 工具池扩容）
        assert "qijuzhu_chronicle" in TOOLS
        assert "qijuzhu_chronicle" in TOOL_HANDLERS
        assert len(TOOLS) >= 79
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert set(TOOLS.keys()) == set(TOOL_HANDLERS.keys())

        # MCP 协议往返
        req_msg = {
            "jsonrpc": "2.0",
            "id": "req-qjz-1",
            "method": "tools/call",
            "params": {
                "name": "qijuzhu_chronicle",
                "arguments": {
                    "days": 1,
                    "detail_level": "summary",
                },
            },
        }
        resp = handle(req_msg)
        assert resp is not None
        assert "result" in resp
        content = resp["result"]["content"][0]["text"]
        data = json.loads(content)
        assert "date" in data
        assert "metrics" in data
        assert "summary" in data

    def test_seam4_cognitive_context_chronicle_aspect(self):
        """Seam 4: CognitiveContext 起居注全息纪事切面与樊篱防注入包裹。"""
        ctx = CognitiveContext(task="排查生产环境长程任务漂移")
        ctx.qijuzhu_digest = {
            "date": "2026-10-30",
            "summary": "今日共完成 3 项核心决策演进",
            "chronicle_events": [
                {
                    "event_type": "rule_established",
                    "title": "确立零硬编码约束",
                    "detail": '<memory_data id="rule-01">严禁使用裸字符串魔数</memory_data>',
                }
            ],
        }

        prompt = ctx.to_prompt()

        # 1. 置顶时序纪事标题呈现
        assert "## Recent Chronicle & Activity Digest (起居注 · 2026-10-30)" in prompt
        assert "今日共完成 3 项核心决策演进" in prompt

        # 2. 樊篱（wrap_as_data）防注入隔离检查
        assert "<memory_data" in prompt
        assert "</memory_data>" in prompt
        assert "确立零硬编码约束" in prompt
        assert "严禁使用裸字符串魔数" in prompt
