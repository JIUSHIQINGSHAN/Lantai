"""端到端不 mock 集成冒烟测试：考功服务层、REST 端点、MCP 91 工具池与认知上下文切面（Ticket 02）。

覆盖 4 个独立接缝：
1. 接缝 1：服务层 kaogong_inspect_service 与 run_kaogong_cycle 调度；
2. 接缝 2：REST 路由端点 POST /kaogong/eval、GET /kaogong/report 与 POST /kaogong/inspect 鉴权与调用；
3. 接缝 3：MCP 协议工具 kaogong_inspect 往返解析与 91 工具池 1:1 双向对齐守卫；
4. 接缝 4：CognitiveContext 认知上下文 kaogong_digest 切面渲染与樊篱防注入围栏包裹。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem
from lantai.services.kaogong_service import kaogong_inspect_service, run_kaogong_cycle


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


def test_seam1_service_layer_kaogong(db_session: Session):
    """Seam 1: 服务层调用 kaogong_inspect_service 与 run_kaogong_cycle。"""
    mem = MemoryItem(
        id="mem_kg_srv_01",
        content="高可用 PostgreSQL 物理复制槽运维规程",
        use_count=5,
        helpful_count=5,
        importance=0.6,
        tier="working",
        status="active",
        user_id="default",
    )
    db_session.add(mem)
    db_session.commit()

    # 只读审查
    inspect_res = kaogong_inspect_service(
        memory_id="mem_kg_srv_01", session=db_session, write=False
    )
    assert inspect_res["memory_id"] == "mem_kg_srv_01"
    assert inspect_res["action"] == "promote_longterm"
    assert inspect_res["new_tier"] == "longterm"
    # 验证未写库
    db_mem = db_session.get(MemoryItem, "mem_kg_srv_01")
    assert db_mem.tier == "working"

    # 写库应用
    inspect_write = kaogong_inspect_service(
        memory_id="mem_kg_srv_01", session=db_session, write=True
    )
    assert inspect_write["action"] == "promote_longterm"
    db_mem_updated = db_session.get(MemoryItem, "mem_kg_srv_01")
    assert db_mem_updated.tier == "longterm"


def test_seam2_rest_endpoints(monkeypatch):
    """Seam 2: REST 端点 POST /kaogong/eval, GET /kaogong/report, POST /kaogong/inspect。"""
    client = TestClient(app)

    # 1. POST /kaogong/eval
    r1 = client.post("/kaogong/eval", json={"limit": 50}, headers={"X-API-Key": "test"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert "evaluated" in d1

    # 2. GET /kaogong/report
    r2 = client.get("/kaogong/report", headers={"X-API-Key": "test"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert "promoted_longterm" in d2

    # 3. POST /kaogong/inspect 异常处理（不存在的 memory_id）
    r3 = client.post(
        "/kaogong/inspect",
        json={"memory_id": "non_existent_mid", "write": False},
        headers={"X-API-Key": "test"},
    )
    assert r3.status_code in (404, 422)


def test_seam3_mcp_91_tools_parity():
    """Seam 3: MCP 协议往返与 91+ 工具池 1:1 双向对齐守卫。"""
    assert len(TOOLS) >= 91
    assert len(TOOL_HANDLERS) == len(TOOLS)
    assert "kaogong_inspect" in TOOLS
    assert "kaogong_inspect" in TOOL_HANDLERS
    assert "kaogong_eval" in TOOLS
    assert "kaogong_eval" in TOOL_HANDLERS

    # 双向对齐严格断言
    for t_name in TOOLS:
        assert t_name in TOOL_HANDLERS, f"Tool {t_name} missing from TOOL_HANDLERS"
    for h_name in TOOL_HANDLERS:
        assert h_name in TOOLS, f"Handler {h_name} missing from TOOLS"

    # 测试 tools/list 包含 kaogong_inspect
    resp_list = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert resp_list is not None
    names = [t["name"] for t in resp_list["result"]["tools"]]
    assert "kaogong_inspect" in names
    assert "kaogong_eval" in names


def test_seam4_cognitive_context_kaogong_aspect():
    """Seam 4: CognitiveContext kaogong_digest 结构化切面渲染与樊篱包裹。"""
    items = [
        {
            "memory_id": "mem_kg_ctx_01",
            "action": "promote_longterm",
            "score": 0.88,
            "reason": "高频高采纳晋升",
            "fenced_content": '<memory_data id="mem_kg_ctx_01" score="0.88">Kubernetes Pod QoS 配置指南</memory_data>',
        }
    ]
    ctx = CognitiveContext(task="测试考功切面", kaogong_digest=items)
    prompt = ctx.to_prompt()

    assert "## Memory Merit & Value Evolution (考功 · 1):" in prompt
    assert "- [考功·K1 (promote_longterm) [功过分: 0.88]]" in prompt
    assert '<memory_data id="mem_kg_ctx_01" score="0.88">' in prompt
    assert "Kubernetes Pod QoS 配置指南" in prompt
