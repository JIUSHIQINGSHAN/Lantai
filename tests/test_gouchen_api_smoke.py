"""端到端不 mock 集成冒烟测试：钩沉服务层封装、REST 端点集成、MCP 95 工具池扩充与认知上下文切面融合 (Ticket 02)。

覆盖 4 个独立接缝：
1. 接缝 1 (服务层)：gouchen_dredge_service 与 gouchen_snapshot_service 调度验证（真实 SQLite）；
2. 接缝 2 (REST 端点)：POST /gouchen/dredge 与 GET /gouchen/snapshot 协议往返；
3. 接缝 3 (MCP 95 工具池)：MCP 协议往返、新增 gouchen_dredge 工具与 95 工具 1:1 双向对齐守卫；
4. 接缝 4 (认知上下文切面)：CognitiveContext gouchen_digest 切面渲染与樊篱防注入安全围栏包裹。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.gouchen_service import (
    gouchen_dredge_service,
    gouchen_snapshot_service,
)


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seam1_service_layer_gouchen(db_session: Session):
    """接缝 1：服务层 gouchen_dredge_service 与 gouchen_snapshot_service 调度验证。"""
    principal = Principal(user_id="alice_gc", tenant_id="t_gc")
    now = utcnow()

    # 构造沉埋记忆（90天前归档）
    mem_sink = MemoryItem(
        id="gc_svc_sink",
        content="早期遗留的架构基线配置",
        status="archived",
        decay_score=0.05,
        user_id="alice_gc",
        tenant_id="t_gc",
        created_at=now - timedelta(days=90),
    )
    # 构造历史快照记忆（50天前生效）
    t0 = now - timedelta(days=50)
    mem_hist = MemoryItem(
        id="gc_svc_hist",
        content="历史时刻的数据库主机配置",
        status="active",
        valid_from=t0 - timedelta(days=5),
        valid_to=t0 + timedelta(days=30),
        created_at=t0 - timedelta(days=5),
        user_id="alice_gc",
        tenant_id="t_gc",
    )
    db_session.add(mem_sink)
    db_session.add(mem_hist)
    db_session.commit()

    # 1. 沉埋记忆打捞服务
    res = gouchen_dredge_service(
        query="架构",
        min_sunken_score=0.5,
        session=db_session,
        principal=principal,
    )
    assert res["total_dredged"] >= 1
    hit_ids = [it["memory_id"] for it in res["items"]]
    assert "gc_svc_sink" in hit_ids
    assert "<memory_data" in res["fenced_content"]

    # 2. 历史快照服务（以 t0 为锚点）
    snap = gouchen_snapshot_service(
        anchor_time=t0,
        session=db_session,
        principal=principal,
    )
    assert snap["total_count"] >= 1
    snap_ids = [m["memory_id"] for m in snap["memories"]]
    assert "gc_svc_hist" in snap_ids
    assert "<memory_data" in snap["fenced_content"]


def test_seam2_rest_endpoints():
    """接缝 2：REST 路由端点 POST /gouchen/dredge 与 GET /gouchen/snapshot。"""
    client = TestClient(app)

    # 1. POST /gouchen/dredge（空库打捞，期望 200 + 结构正确）
    r1 = client.post(
        "/gouchen/dredge",
        json={"query": "", "min_sunken_score": 0.4, "limit": 10},
        headers={"X-API-Key": "test"},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert "total_dredged" in body1
    assert "items" in body1

    # 2. GET /gouchen/snapshot（合法 anchor_time）
    anchor = (utcnow() - timedelta(days=30)).isoformat()
    r2 = client.get(
        "/gouchen/snapshot",
        params={"anchor_time": anchor, "limit": 10},
        headers={"X-API-Key": "test"},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert "total_count" in body2
    assert "memories" in body2


def test_seam3_mcp_95_tools_parity():
    """接缝 3：MCP 95 工具 gouchen_dredge 注册 + TOOLS/TOOL_HANDLERS 1:1 对齐守卫。"""
    # 工具总数验证
    assert len(TOOLS) == 95, f"Expected 95 tools, got {len(TOOLS)}"
    assert len(TOOL_HANDLERS) == len(TOOLS), (
        f"TOOLS ({len(TOOLS)}) ≠ TOOL_HANDLERS ({len(TOOL_HANDLERS)}): "
        f"missing={set(TOOLS) - set(TOOL_HANDLERS)}, "
        f"extra={set(TOOL_HANDLERS) - set(TOOLS)}"
    )

    # gouchen_dredge 工具存在性验证
    assert "gouchen_dredge" in TOOLS
    assert "gouchen_dredge" in TOOL_HANDLERS

    # MCP 协议往返：dredge 模式
    mcp_req = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "gouchen_dredge",
            "arguments": {"action": "dredge", "query": "", "min_sunken_score": 0.4, "limit": 5},
        },
        "id": 1,
    }
    resp = handle(mcp_req)
    assert resp.get("error") is None, f"MCP error: {resp.get('error')}"
    content = resp.get("result", {}).get("content", [])
    assert any(c.get("type") == "text" for c in content)


def test_seam4_cognitive_context_gouchen_digest():
    """接缝 4：CognitiveContext.gouchen_digest 切面渲染与樊篱防注入安全围栏包裹。"""
    gc_digest = {
        "query": "架构",
        "total_dredged": 2,
        "items": [
            {
                "memory_id": "gc_ctx_01",
                "content": "遗留架构基线",
                "sunken_score": 0.72,
                "fenced_content": "<memory_data id='gouchen-gc_ctx_01'>遗留架构基线</memory_data>",
            }
        ],
        "fenced_content": "<memory_data id='gouchen-summary'>钩沉打捞摘要</memory_data>",
    }

    ctx = CognitiveContext(task="复盘历史架构决策", gouchen_digest=gc_digest)
    rendered = ctx.to_prompt()

    # 验证钩沉切面已渲染
    assert "钩沉" in rendered
    assert "G1" in rendered
    # 验证沉没度信息
    assert "0.72" in rendered
    # 验证樊篱围栏包裹
    assert "<memory_data" in rendered
