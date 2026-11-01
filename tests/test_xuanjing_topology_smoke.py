"""悬镜·3D 神经元记忆星图与力导向拓扑服务冒烟测试 (Ticket 01)

基于真实 in-memory SQLite，不 mock 内部计算逻辑。
验证接缝（Seams）：
1. 节点提取与多维属性计算：衰减度（decay_score）、活跃引力（weight/importance）、生命周期与同袍作用域；
2. 拓扑边提取：包含格物因果边（causes/triggered_by/leads_to）与传统边（supports/refines/contradicts/supersedes）；
3. 星系/聚簇聚合（clusters）：包含场景（MemoryScene）和泳道（lane）分类与质心统计；
4. 租户隔离与同袍记忆继承：跨租户阻断，同租户放行私有记忆与 swarm_shared 共享记忆；
5. HTTP 路由端点 `GET /graph/xuanjing` 与 MCP 工具 `get_xuanjing_graph` 契约验证。
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.auth import Principal, get_current_user
from lantai.models.enums import EdgeRelation
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryScene
from lantai.services.xuanjing_service import build_xuanjing_topology, get_xuanjing_graph


@pytest.fixture()
def xuanjing_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    return session_factory, engine


def _create_mem(
    mid: str,
    content: str,
    lane: str = "fact",
    scene_id: str | None = None,
    user_id: str = "user-1",
    tenant_id: str = "t-1",
    agent_id: str | None = None,
    shared_scope: str = "private",
    decay_score: float = 0.9,
    importance: float = 0.8,
    use_count: int = 5,
    status: str = "active",
) -> MemoryItem:
    return MemoryItem(
        id=mid,
        memory_type="semantic",
        key=f"k-{mid}",
        content=content,
        lane=lane,
        scene_id=scene_id,
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        shared_scope=shared_scope,
        decay_score=decay_score,
        importance=importance,
        use_count=use_count,
        helpful_count=2,
        status=status,
        created_at=datetime.now(UTC) - timedelta(days=2),
        updated_at=datetime.now(UTC) - timedelta(hours=1),
    )


def _create_edge(
    eid: str,
    src: str,
    tgt: str,
    relation: str = "supports",
    confidence: float = 0.85,
    tenant_id: str = "t-1",
    user_id: str = "user-1",
) -> MemoryEdge:
    return MemoryEdge(
        id=eid,
        source_memory_id=src,
        target_memory_id=tgt,
        relation=relation,
        confidence=confidence,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def test_build_xuanjing_topology_basic(xuanjing_env):
    """验证基础拓扑：节点衰减/引力计算、边关系及场景星系聚簇。"""
    session_factory, _ = xuanjing_env
    with session_factory() as s:
        s.add(MemoryScene(id="scene-alpha", name="项目发布"))
        s.add(
            _create_mem(
                "m1", "核心发布计划", scene_id="scene-alpha", decay_score=0.95, importance=0.9
            )
        )
        s.add(
            _create_mem(
                "m2", "引发代码审查", scene_id="scene-alpha", decay_score=0.7, importance=0.6
            )
        )
        s.add(_create_mem("m3", "导致最终上线", scene_id=None, decay_score=0.5, importance=0.5))
        # 添加因果与支持拓扑边
        s.add(_create_edge("e1", "m1", "m2", relation=EdgeRelation.LEADS_TO))
        s.add(_create_edge("e2", "m2", "m3", relation=EdgeRelation.CAUSES))
        s.commit()

    principal = Principal(user_id="user-1", tenant_id="t-1", role="user")
    topo = build_xuanjing_topology(session_factory(), principal=principal)

    assert "nodes" in topo
    assert "edges" in topo
    assert "clusters" in topo
    assert "stats" in topo

    node_map = {n["id"]: n for n in topo["nodes"]}
    assert len(node_map) == 3
    assert "m1" in node_map
    assert "m2" in node_map
    assert "m3" in node_map

    # 验证节点渲染属性
    n1 = node_map["m1"]
    assert n1["decay_score"] == 0.95
    assert n1["scene_id"] == "scene-alpha"
    assert n1["weight"] > 1.0  # 结合 importance 与 use_count
    assert n1["shared_scope"] == "private"

    # 验证边信息
    assert len(topo["edges"]) == 2
    rel_set = {e["relation"] for e in topo["edges"]}
    assert EdgeRelation.LEADS_TO in rel_set
    assert EdgeRelation.CAUSES in rel_set

    # 验证聚簇
    cluster_ids = {c["id"] for c in topo["clusters"]}
    assert "scene-alpha" in cluster_ids


def test_xuanjing_tenant_isolation_and_tongpao_sharing(xuanjing_env):
    """验证租户隔离与同袍 swarm_shared 跨 Agent 放行。"""
    session_factory, _ = xuanjing_env
    with session_factory() as s:
        # 租户 t-1 下 agent-A 的私有记忆
        s.add(
            _create_mem(
                "m-priv-a",
                "Agent A 私有操作备忘",
                agent_id="agent-A",
                user_id="user-1",
                tenant_id="t-1",
            )
        )
        # 租户 t-1 下公共知识殿堂记忆
        s.add(
            _create_mem(
                "m-shared",
                "公共知识殿堂规约",
                agent_id="agent-A",
                shared_scope="swarm_shared",
                user_id="user-1",
                tenant_id="t-1",
            )
        )
        # 租户 t-1 下 agent-B 的私有记忆
        s.add(
            _create_mem(
                "m-priv-b",
                "Agent B 私有操作备忘",
                agent_id="agent-B",
                user_id="user-1",
                tenant_id="t-1",
            )
        )
        # 外部租户 t-2 下的共享记忆（绝不可泄露）
        s.add(
            _create_mem(
                "m-alien",
                "外部租户公共知识",
                shared_scope="swarm_shared",
                user_id="user-2",
                tenant_id="t-2",
            )
        )
        s.commit()

    # Agent B 观察悬镜拓扑：应看到自身私有记忆与同租户公共记忆，看不到 Agent A 私有记忆或外部租户记忆
    principal_b = Principal(user_id="user-1", tenant_id="t-1", agent_id="agent-B", role="user")
    topo = build_xuanjing_topology(session_factory(), principal=principal_b)

    node_ids = {n["id"] for n in topo["nodes"]}
    assert "m-priv-b" in node_ids
    assert "m-shared" in node_ids
    assert "m-priv-a" not in node_ids
    assert "m-alien" not in node_ids


def test_xuanjing_api_and_mcp_contracts(xuanjing_env, monkeypatch):
    """验证 REST 路由 /graph/xuanjing 与 MCP 工具 get_xuanjing_graph。"""
    session_factory, _ = xuanjing_env
    import lantai.storage.db as db_module
    from lantai.api.app import app
    from lantai.cli.mcp import handle

    monkeypatch.setattr(db_module, "get_session", session_factory)

    with session_factory() as s:
        s.add(_create_mem("m-api", "API 端点测试节点", decay_score=0.88, importance=0.7))
        s.commit()

    test_user = Principal(user_id="user-1", tenant_id="t-1", role="user")
    # setitem 随用例撤销；直接赋值会把 user-1/t-1 身份留给后续所有 API 测试
    # （2026-10-31 实证：test_zuozheng_api_smoke 因此 404）
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: test_user)
    client = TestClient(app)

    # 1. 验证 REST 端点
    resp = client.get("/graph/xuanjing?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert any(n["id"] == "m-api" for n in data["nodes"])

    # 2. 验证 MCP 工具
    mcp_req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/call",
        "params": {
            "name": "get_xuanjing_graph",
            "arguments": {"limit": 10, "user_id": "user-1"},
        },
    }
    mcp_resp = handle(mcp_req)
    assert mcp_resp is not None
    assert "result" in mcp_resp
    mcp_result = mcp_resp["result"]
    assert "content" in mcp_result
    payload = json.loads(mcp_result["content"][0]["text"])
    assert "nodes" in payload
    assert any(n["id"] == "m-api" for n in payload["nodes"])
