"""尘封·专栏 API、MCP 工具集成与归档穿透追溯冒烟测试 (Ticket 02)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用任何内部逻辑 mock。
验证公共接口 Seams：
1. ChenfengEngine 检索与解封: search_archived 归档专属检索与 unarchive_memory 恢复 active 并核验在场；
2. CognitiveContext 尘封切面集成: 在认知上下文中嵌入 archived_history 切面，并渲染 Markdown Prompt；
3. HTTP 路由端点: 验证 GET /memories/archived 与 POST /memories/{memory_id}/unarchive（含跨租户 403 阻断）；
4. MCP 工具协议: 验证 memory_archived_search 与 memory_unarchive 经由 JSON-RPC 2.0 正确调用与行级隔离。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cli.mcp import handle
from lantai.cognition.chenfeng import (
    ChenfengEngine,
    search_archived_memories,
    unarchive_memory_entry,
)
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem
from lantai.storage.fts import fts_has_row


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes or ["fact", "rule", "general"],
        role="admin" if admin else "user",
    )


def _seed_chenfeng_data(param_env):
    """灌入真实的尘封历史归档数据。"""
    t1 = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
    t2 = datetime(2025, 6, 20, 14, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    session_factory, _ = param_env
    with session_factory() as s:
        # Alice 的尘封记忆 1: 旧版数据库配置
        s.add(
            MemoryItem(
                id="mem_archived_db",
                content="旧版 MySQL 5.7 字符集配置为 utf8 而非 utf8mb4",
                lane="fact",
                status="archived",
                user_id="alice",
                scene_id="infrastructure",
                created_at=t1,
                updated_at=t1,
            )
        )
        # Alice 的尘封记忆 2: 旧部署规程
        s.add(
            MemoryItem(
                id="mem_archived_deploy",
                content="早期的 Jenkins 单机构建与 Shell 部署规程",
                lane="rule",
                status="archived",
                user_id="alice",
                scene_id="devops",
                created_at=t2,
                updated_at=t2,
            )
        )
        # Alice 的活跃记忆: Kubernetes 现代部署
        s.add(
            MemoryItem(
                id="mem_active_k8s",
                content="全面推行 GitOps 与 ArgoCD 声明式发布",
                lane="rule",
                status="active",
                user_id="alice",
                scene_id="devops",
                created_at=t3,
                updated_at=t3,
            )
        )
        # Bob 的尘封记忆: 私密历史账单（隔离测试）
        s.add(
            MemoryItem(
                id="bob_archived_secret",
                content="Bob 2024 年度未报销差旅明细",
                lane="fact",
                status="archived",
                user_id="bob",
                created_at=t1,
                updated_at=t1,
            )
        )
        s.commit()


def test_chenfeng_engine_search_and_unarchive_seam(param_env):
    """Seam 1: 验证 ChenfengEngine 专用归档检索与解封恢复。"""
    _seed_chenfeng_data(param_env)
    session_factory, _ = param_env
    user_alice = _principal("alice")

    with session_factory() as s:
        engine = ChenfengEngine(session=s, principal=user_alice)

        # 1. 检索归档档案：关键词过滤
        res_kw = engine.search_archived(query="MySQL 字符集")
        assert res_kw["total"] == 1
        assert res_kw["items"][0]["id"] == "mem_archived_db"

        # 2. 检索归档档案：分轨与场景过滤
        res_filter = engine.search_archived(lane="rule", scene="devops")
        assert res_filter["total"] == 1
        assert res_filter["items"][0]["id"] == "mem_archived_deploy"

        # 3. 租户隔离：Alice 查不到 Bob 的归档条目
        res_all = engine.search_archived()
        found_ids = {item["id"] for item in res_all["items"]}
        assert "bob_archived_secret" not in found_ids

        # 4. 解封恢复：mem_archived_db -> active
        res_unarchive = engine.unarchive_memory(
            memory_id="mem_archived_db",
            reason="排查历史字符集兼容性故障，恢复活跃参考",
        )
        assert res_unarchive["ok"] is True
        item = s.get(MemoryItem, "mem_archived_db")
        assert item.status == "active"
        # 确认 FTS 在场补齐
        assert fts_has_row(s, "mem_archived_db") is True


def test_cognitive_context_archived_history_seam(param_env):
    """Seam 2: 验证认知上下文对尘封历史溯源切面 (archived_history) 的支持。"""
    _seed_chenfeng_data(param_env)

    ctx = CognitiveContext(task="排查生产数据库字符集乱码历史根因")
    assert hasattr(ctx, "archived_history")

    # 挂载尘封档案记录并生成提示词
    ctx.archived_history = [
        {
            "id": "mem_archived_db",
            "content": "旧版 MySQL 5.7 字符集配置为 utf8 而非 utf8mb4",
            "archive_reason": "2025年废弃归档",
        }
    ]

    prompt = ctx.to_prompt()
    assert "## Archived History & Precedents" in prompt
    assert "旧版 MySQL 5.7 字符集配置为 utf8" in prompt
    assert "mem_archived_db" in prompt


def test_http_api_archived_search_and_unarchive_seam(param_env):
    """Seam 3: 验证 REST 端点 GET /memories/archived 与 POST /memories/{memory_id}/unarchive。"""
    _seed_chenfeng_data(param_env)
    user_alice = _principal("alice")
    client = TestClient(app)

    app.dependency_overrides[get_current_user] = lambda: user_alice
    try:
        # 1. GET /memories/archived 检索归档
        resp = client.get("/memories/archived?query=Jenkins")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == "mem_archived_deploy"

        # 2. POST /memories/{memory_id}/unarchive 正常解封
        resp_unarchive = client.post(
            "/memories/mem_archived_deploy/unarchive",
            json={"reason": "重新参考旧构建规程"},
        )
        assert resp_unarchive.status_code == 200
        assert resp_unarchive.json()["ok"] is True

        # 3. 越权防御：Alice 试图解封 Bob 的私密归档 -> 403 阻断
        resp_hack = client.post(
            "/memories/bob_archived_secret/unarchive",
            json={"reason": "非法解封"},
        )
        assert resp_hack.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_archived_search_and_unarchive_seam(param_env):
    """Seam 4: 验证 MCP 协议工具调用与租户边界隔离。"""
    _seed_chenfeng_data(param_env)

    # 1. Alice 正常检索尘封档案
    mcp_search = handle(
        {
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {
                "name": "memory_archived_search",
                "arguments": {"query": "Jenkins", "user_id": "alice"},
            },
        }
    )
    assert "error" not in mcp_search
    text_search = mcp_search["result"]["content"][0]["text"]
    assert "mem_archived_deploy" in text_search

    # 2. Alice 正常调用解封工具
    mcp_unarchive = handle(
        {
            "jsonrpc": "2.0",
            "id": 302,
            "method": "tools/call",
            "params": {
                "name": "memory_unarchive",
                "arguments": {
                    "memory_id": "mem_archived_deploy",
                    "reason": "需要复现旧版问题",
                    "user_id": "alice",
                },
            },
        }
    )
    assert "error" not in mcp_unarchive
    text_unarchive = mcp_unarchive["result"]["content"][0]["text"]
    assert "解封" in text_unarchive or "ok" in text_unarchive

    # 3. Alice 越权解封 Bob 的记忆 -> 阻断
    mcp_hack = handle(
        {
            "jsonrpc": "2.0",
            "id": 303,
            "method": "tools/call",
            "params": {
                "name": "memory_unarchive",
                "arguments": {
                    "memory_id": "bob_archived_secret",
                    "reason": "非法跨租户解封",
                    "user_id": "alice",
                },
            },
        }
    )
    assert "error" not in mcp_hack
    text_hack = mcp_hack["result"]["content"][0]["text"]
    assert any(
        k in text_hack for k in ("无权", "失败", "not allowed", "another user", "forbidden", "403")
    )
