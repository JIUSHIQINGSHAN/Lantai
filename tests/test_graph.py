"""记忆关系图（v0.9 MAP 星图）测试。

build_graph 纯函数直调不 mock；用真实 SQLite 验证节点入选规则、
边过滤（跨池边丢弃）与 supersedes 链保留。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from lantai.models.tables import MemoryEdge, MemoryItem, MemoryScene, RawDocument


@pytest.fixture()
def graph_env():
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    return session_factory, engine


def _m(mid, content, lane="fact", scene_id=None, status="active",
       updated_days=0):
    return MemoryItem(
        id=mid, memory_type="semantic", key=f"k-{mid}", content=content,
        lane=lane, scene_id=scene_id, status=status, importance=0.5,
        decay_score=1.0, decay_class="episodic", use_count=0,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
        updated_at=datetime.now(timezone.utc) - timedelta(days=updated_days),
    )


def _edge(edge_id, source, target, relation, confidence=0.8):
    return MemoryEdge(
        id=edge_id, source_memory_id=source, target_memory_id=target,
        relation=relation, confidence=confidence,
    )


def _node(out, mid):
    return next(n for n in out["nodes"] if n["id"] == mid)


# ── 纯函数：节点入选 / 边过滤 / 统计（真实 SQLite 直调）────────

def test_build_graph_pure_shape(graph_env):
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("m1", "发布会在周五下午两点开始"))
        s.add(_m("m2", "发布会需要提前一天彩排"))
        s.add(_edge("e1", "m1", "m2", "supports"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    assert out["stats"]["lane_counts"] == {"fact": 2}
    assert out["stats"]["edge_counts"] == {"supports": 1}
    assert {n["id"] for n in out["nodes"]} == {"m1", "m2"}
    assert out["links"][0]["relation"] == "supports"
    assert out["links"][0]["confidence"] == 0.8


def test_isolated_memory_excluded_scene_members_included(graph_env):
    """孤立记忆（无边的非 scene 成员）不上图；scene 成员即使无边也入选。"""
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("m1", "有关系的记忆"))
        s.add(_m("m2", "有关系的记忆二"))
        s.add(_m("m3", "孤立记忆"))
        s.add(_m("m4", "场景成员但无边", scene_id="sc1"))
        s.add(MemoryScene(id="sc1", name="发布准备"))
        s.add(_edge("e1", "m1", "m2", "refines"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    ids = {n["id"] for n in out["nodes"]}
    assert ids == {"m1", "m2", "m4"}
    assert out["scenes"] == {"sc1": "发布准备"}
    assert _node(out, "m4")["scene_id"] == "sc1"
    assert out["stats"]["lane_counts"]["fact"] == 3


def test_cross_pool_edge_dropped_and_archived_excluded(graph_env):
    """limit 截断后跨池边丢弃；archived 记忆不入选、其边不保留。"""
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("m1", "活跃记忆一", updated_days=0))
        s.add(_m("m2", "活跃记忆二", updated_days=5))
        s.add(_m("old", "归档旧记忆", status="archived", updated_days=30))
        # m1 <-> old 的边：old 不在候选池（非 active），整边丢弃
        s.add(_edge("e1", "m1", "old", "supersedes"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory(), limit=2)
    # m1/m2 无有效边且无 scene -> 无节点；归档 old 及其边均不出现
    assert out["nodes"] == []
    assert out["links"] == []
    assert out["stats"] == {"lane_counts": {}, "node_type_counts": {}, "edge_counts": {}}


def test_supersedes_chain_kept(graph_env):
    """supersedes 链：v1 -> v2 -> v3 逐级保留，边不因中间节点缺失而断裂。"""
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("v1", "旧结论"))
        s.add(_m("v2", "修订结论"))
        s.add(_m("v3", "最新结论"))
        s.add(_edge("e1", "v1", "v2", "supersedes"))
        s.add(_edge("e2", "v2", "v3", "supersedes"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    assert len(out["links"]) == 2
    rels = {(l["source"], l["target"]) for l in out["links"]}
    assert rels == {("v1", "v2"), ("v2", "v3")}
    assert out["stats"]["edge_counts"]["supersedes"] == 2


def test_build_graph_invalid_limit_raises(graph_env):
    """非法 limit（越界/bool/非 int）抛 ValueError，不静默钳制（宁 miss 不脏写）。"""
    session_factory, _ = graph_env
    from lantai.ops.graph import build_graph, validate_graph_limit
    with pytest.raises(ValueError):
        validate_graph_limit(0)
    with pytest.raises(ValueError):
        validate_graph_limit(501)
    with pytest.raises(ValueError):
        validate_graph_limit(True)  # bool 不是 int
    with pytest.raises(ValueError):
        build_graph(session_factory(), limit=9999)
    assert validate_graph_limit(150) == 150


def test_empty_db_returns_empty_graph(graph_env):
    session_factory, _ = graph_env
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    assert out["nodes"] == []
    assert out["links"] == []
    assert out["stats"] == {"lane_counts": {}, "node_type_counts": {}, "edge_counts": {}}


def test_source_document_nodes_included(graph_env):
    """来源文档（doc_* RawDocument）节点入选：label=title、带 url、node_type=source。"""
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("m1", "发布会记忆"))
        s.add(RawDocument(id="doc_1", source_type="web", source_id="src1", title="发布会指南",
                          content="发布会指南全文", content_hash="h1",
                          url="https://example.com/guide"))
        s.add(_edge("e1", "doc_1", "m1", "supports"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    types = {n["id"]: n["node_type"] for n in out["nodes"]}
    assert types == {"m1": "memory", "doc_1": "source"}
    src = _node(out, "doc_1")
    assert src["label"] == "发布会指南"
    assert src["url"] == "https://example.com/guide"
    assert src["lane"] is None and src["decay_class"] is None
    assert out["stats"]["node_type_counts"] == {"memory": 1, "source": 1}
    assert out["stats"]["lane_counts"] == {"fact": 1}
    assert len(out["links"]) == 1
    assert out["links"][0]["source"] == "doc_1"


def test_doc_to_archived_memory_edge_dropped(graph_env):
    """来源文档 -> archived 记忆的边丢弃（记忆端不在池，宁 miss 不上图）。"""
    session_factory, _ = graph_env
    with session_factory() as s:
        s.add(_m("old", "归档记忆", status="archived"))
        s.add(RawDocument(id="doc_1", source_type="web", source_id="src2", title="旧文档",
                          content="x", content_hash="h1", url="https://example.com/old"))
        s.add(_edge("e1", "doc_1", "old", "supports"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory())
    assert out["nodes"] == []
    assert out["links"] == []


def test_limit_respected(graph_env):
    session_factory, _ = graph_env
    with session_factory() as s:
        for i in range(5):
            s.add(_m(f"m{i}", f"记忆{i}", updated_days=i))
        # 两两成边，确保入选；limit=2 时只有最新两条
        s.add(_edge("e1", "m0", "m1", "supports"))
        s.add(_edge("e2", "m0", "m2", "supports"))
        s.add(_edge("e3", "m0", "m3", "supports"))
        s.add(_edge("e4", "m0", "m4", "supports"))
        s.commit()
    from lantai.ops.graph import build_graph
    out = build_graph(session_factory(), limit=2)
    # 池内最新两条 m0(updated 0天前最新) 与 m1 —— 但 m0 与 m1 有边，入选
    ids = {n["id"] for n in out["nodes"]}
    assert ids == {"m0", "m1"}
    assert len(out["links"]) == 1
    assert out["links"][0]["relation"] == "supports"
