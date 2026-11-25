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
from lantai.services.xuanjing_service import (
    build_xuanjing_topology,
    calculate_decay_curve,
    calculate_halo,
    get_xuanjing_changes,
    get_xuanjing_graph,
)

pytestmark = pytest.mark.mcp_full_surface()


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
    tags: list[str] | None = None,
    domain: str = "user",
) -> MemoryItem:
    return MemoryItem(
        id=mid,
        memory_type="semantic",
        key=f"k-{mid}",
        content=content,
        lane=lane,
        domain=domain,
        tags=tags or [],
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
    from lantai.cli.mcp import handle, handle_get_xuanjing_graph

    monkeypatch.setattr(db_module, "get_session", session_factory)

    with session_factory() as s:
        s.add(_create_mem("m-api", "API 端点测试节点", decay_score=0.88, importance=0.7))
        s.commit()

    test_user = Principal(user_id="user-1", tenant_id="t-1", role="user")
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
    with pytest.raises(ValueError, match="integer"):
        handle_get_xuanjing_graph({"limit": True})
    with pytest.raises(ValueError, match="boolean"):
        handle_get_xuanjing_graph({"include_archived": "false"})

    # 3. 增量路由必须复用快照游标并经过响应模型校验
    snapshot = client.get("/graph/xuanjing?limit=10&domain=project&tag=release")
    assert snapshot.status_code == 200
    snapshot_data = snapshot.json()

    with session_factory() as s:
        changed = _create_mem(
            "m-api-change",
            "API 增量过滤测试节点",
            domain="project",
            tags=["release"],
        )
        archived = _create_mem(
            "m-api-archived",
            "API 归档事件节点",
            domain="project",
            tags=["release"],
            status="archived",
        )
        changed_at = datetime.now(UTC)
        changed.updated_at = changed_at
        archived.updated_at = changed_at
        s.add_all([changed, archived])
        s.commit()

    changes = client.get(
        "/graph/xuanjing/changes",
        params={
            "cursor": snapshot_data["stream_cursor"],
            "limit": 10,
            "domain": "project",
            "tag": "release",
        },
    )
    assert changes.status_code == 200
    changes_data = changes.json()
    assert {event["type"] for event in changes_data["events"]} >= {
        "memory.upsert",
        "memory.remove",
    }
    assert all("id" in event["data"] for event in changes_data["events"])


def test_decay_curve_and_activity_halo_models():
    curve = calculate_decay_curve("episodic", base_score=0.8, days=(0, 30, 60))
    assert curve == [
        {"days": 0, "score": 0.8},
        {"days": 30, "score": 0.4},
        {"days": 60, "score": 0.2},
    ]
    assert calculate_decay_curve("procedural", base_score=0.8, days=(0, 30)) == [
        {"days": 0, "score": 0.8},
        {"days": 30, "score": 0.8},
    ]
    assert calculate_halo(decay_score=0.25, weight=1.0) == {
        "radius": 2.828,
        "intensity": 0.125,
        "opacity": 0.125,
    }
    assert calculate_halo(decay_score=1.0, weight=1e12)["radius"] == 64.0


def test_topology_filters_use_exact_tags_and_theme_and_keyset_pages(xuanjing_env):
    session_factory, _ = xuanjing_env
    with session_factory() as s:
        s.add(
            _create_mem(
                "m-a",
                "部署记录",
                lane="rule",
                importance=0.7,
                domain="project",
                tags=["release", "blue"],
            )
        )
        s.add(
            _create_mem(
                "m-b",
                "发布计划",
                lane="rule",
                importance=0.6,
                domain="project",
                tags=["release-notes"],
            )
        )
        s.add(_create_mem("m-c", "个人偏好", lane="preference"))
        s.commit()

    principal = Principal(user_id="user-1", tenant_id="t-1", role="user")
    filtered = build_xuanjing_topology(
        session_factory(), principal=principal, domain="project", lane="rule", tag="release"
    )
    assert [node["id"] for node in filtered["nodes"]] == ["m-a"]
    multi_tagged = build_xuanjing_topology(
        session_factory(), principal=principal, tags=["release", "blue"]
    )
    assert [node["id"] for node in multi_tagged["nodes"]] == ["m-a"]

    themed = build_xuanjing_topology(
        session_factory(), principal=principal, domain="project", theme="部署"
    )
    assert [node["id"] for node in themed["nodes"]] == ["m-a"]

    first = build_xuanjing_topology(session_factory(), principal=principal, limit=2)
    assert first["has_more"] is True
    assert len(first["nodes"]) == 2
    second = build_xuanjing_topology(
        session_factory(), principal=principal, limit=2, cursor=first["next_cursor"]
    )
    assert len(second["nodes"]) == 1
    assert {node["id"] for node in first["nodes"]}.isdisjoint(
        {node["id"] for node in second["nodes"]}
    )


def test_topology_node_projection_includes_space_time_curve_and_halo(xuanjing_env):
    session_factory, _ = xuanjing_env
    created = datetime.now(UTC) - timedelta(days=5)
    valid_from = datetime.now(UTC) - timedelta(days=4)
    with session_factory() as s:
        s.add(MemoryScene(id="scene-space", name="项目星系", centroid=[0.1, 0.2, 0.3]))
        item = _create_mem("m-space", "时空节点", scene_id="scene-space", decay_score=0.8)
        item.event_time = created
        item.valid_from = valid_from
        item.tags = ["release"]
        s.add(item)
        s.commit()

    topology = build_xuanjing_topology(
        session_factory(), principal=Principal(user_id="user-1", tenant_id="t-1")
    )
    node = topology["nodes"][0]
    assert node["coordinates"] == {
        "space": {"scene_id": "scene-space", "lane": "fact"},
        "time": {
            "event_time": node["event_time"],
            "valid_from": node["valid_from"],
            "valid_to": None,
        },
    }
    assert node["decay_curve"][0] == {"days": 0, "score": 0.8}
    assert node["halo"]["intensity"] > 0
    assert {cluster["type"] for cluster in topology["clusters"]} == {"scene", "lane"}


def test_xuanjing_change_feed_paginates_and_emits_content_free_removals(xuanjing_env):
    session_factory, _ = xuanjing_env
    principal = Principal(user_id="user-1", tenant_id="t-1")
    snapshot = get_xuanjing_graph(principal=principal)
    changed_at = datetime.now(UTC)
    with session_factory() as s:
        first = _create_mem("m-change-a", "变更前正文", tags=["keep"])
        second = _create_mem("m-change-b", "待归档正文", tags=["keep"], status="archived")
        first.updated_at = changed_at
        second.updated_at = changed_at
        s.add(first)
        s.add(second)
        s.commit()

    first_page = get_xuanjing_changes(
        session_factory(), cursor=snapshot["stream_cursor"], limit=1, principal=principal
    )
    assert first_page["has_more"] is True
    second_page = get_xuanjing_changes(
        session_factory(), cursor=first_page["next_cursor"], limit=1, principal=principal
    )
    events = first_page["events"] + second_page["events"]
    assert [event["type"] for event in events] == ["memory.upsert", "memory.remove"]
    assert events[0]["data"]["id"] == "m-change-a"
    assert events[1]["data"] == {"id": "m-change-b"}
    assert "待归档正文" not in str(events[1])
    archived_snapshot = get_xuanjing_changes(
        session_factory(),
        cursor=snapshot["stream_cursor"],
        limit=10,
        principal=principal,
        include_archived=True,
    )
    archived_event = next(
        event for event in archived_snapshot["events"] if event["id"] == "m-change-b"
    )
    assert archived_event["type"] == "memory.upsert"


def test_xuanjing_hides_scene_aggregates_and_edges_outside_viewer_scope(xuanjing_env):
    session_factory, _ = xuanjing_env
    with session_factory() as s:
        s.add(
            MemoryScene(
                id="scene-shared-id",
                name="hidden scene name",
                summary="hidden scene summary",
                heat=99,
                member_count=2,
                centroid=[0.91, 0.82],
            )
        )
        s.add(
            _create_mem(
                "m-visible",
                "visible memory",
                scene_id="scene-shared-id",
                user_id="user-1",
            )
        )
        s.add(
            _create_mem(
                "m-hidden",
                "hidden memory",
                scene_id="scene-shared-id",
                user_id="user-2",
                status="archived",
            )
        )
        s.add(
            _create_edge(
                "e-hidden",
                "m-visible",
                "m-hidden",
                user_id="user-2",
            )
        )
        s.commit()

    topology = build_xuanjing_topology(
        session_factory(),
        principal=Principal(user_id="user-1", tenant_id="t-1", agent_id="agent-A"),
    )
    assert [node["id"] for node in topology["nodes"]] == ["m-visible"]
    assert topology["edges"] == []
    scene = next(cluster for cluster in topology["clusters"] if cluster["type"] == "scene")
    assert scene["name"] == "scene-shared-id"
    assert scene["summary"] == ""
    assert scene["centroid"] == []
    assert scene["heat"] == 0
    assert scene["member_count"] == 1


def test_xuanjing_change_feed_merges_edges_and_memories_in_a_bounded_order(xuanjing_env):
    session_factory, _ = xuanjing_env
    principal = Principal(user_id="user-1", tenant_id="t-1")
    snapshot = get_xuanjing_graph(principal=principal)
    changed_at = datetime.now(UTC)
    with session_factory() as s:
        first = _create_mem("m-stream-a", "first stream node")
        second = _create_mem("m-stream-b", "second stream node")
        first.updated_at = changed_at
        second.updated_at = changed_at
        edge = _create_edge("e-stream", first.id, second.id)
        edge.created_at = changed_at
        s.add(first)
        s.add(second)
        s.add(edge)
        s.commit()

    first_page = get_xuanjing_changes(
        session_factory(), cursor=snapshot["stream_cursor"], limit=2, principal=principal
    )
    second_page = get_xuanjing_changes(
        session_factory(), cursor=first_page["next_cursor"], limit=2, principal=principal
    )
    assert len(first_page["events"]) == 2
    assert len(second_page["events"]) == 1
    events = first_page["events"] + second_page["events"]
    assert [event["type"] for event in events] == [
        "edge.created",
        "memory.upsert",
        "memory.upsert",
    ]
    assert len({(event["at"], event["id"]) for event in events}) == 3
    assert second_page["has_more"] is False
