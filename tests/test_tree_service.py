"""记忆分类树（v0.7 Ticket 01）测试。

纯函数直调不 mock；落库用真实临时 SQLite（仅 patch db.get_session）。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem, MemoryNode
from lantai.services import tree_service


@pytest.fixture()
def tree_env():
    """内存 SQLite 真实建表（分类树不涉及外部依赖，仅 patch db.get_session）。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _mem(s, mid, content, lane="fact", status="active"):
    now = datetime.now(timezone.utc)
    s.add(MemoryItem(
        id=mid, memory_type="semantic", key=f"k-{mid}", content=content,
        lane=lane, status=status, importance=0.5, decay_score=1.0,
        decay_class="episodic", use_count=0,
        created_at=now - timedelta(days=1), updated_at=now,
    ))


# ── 纯函数 ─────────────────────────────────────────────

def test_validate_node_name():
    assert tree_service.validate_node_name("  发布  ") == "发布"
    with pytest.raises(ValueError):
        tree_service.validate_node_name("")
    with pytest.raises(ValueError):
        tree_service.validate_node_name("a/b")
    with pytest.raises(ValueError):
        tree_service.validate_node_name("a\\b")


def test_build_node_path():
    assert tree_service.build_node_path("/", "projects") == ("/projects", 1)
    assert tree_service.build_node_path("/projects", "release") == ("/projects/release", 2)
    # 父路径规范化
    assert tree_service.build_node_path("projects//", "x") == ("/projects/x", 2)


def test_compute_attachments_prefix_not_substring():
    """/a 不能误匹配 /ab；/a/x 计入 /a 子树。"""
    nodes = [MemoryNode(id="n1", name="a", node_path="/a", depth=1),
             MemoryNode(id="n2", name="ab", node_path="/ab", depth=1)]
    rows = [("/a", 2), ("/ab", 3), ("/a/x", 1)]
    out = tree_service.compute_attachments(rows, nodes)
    assert out["n1"] == {"direct": 2, "subtree": 3}
    assert out["n2"] == {"direct": 3, "subtree": 3}


# ── 落库（真实 SQLite 直调）────────────────────────────

def test_add_node_and_subtree(tree_env):
    session_factory, _ = tree_env
    with session_factory() as s:
        r = tree_service.add_node(s, "projects")
        assert r["node"]["node_path"] == "/projects"
        assert r["node"]["depth"] == 1
        r2 = tree_service.add_node(s, "release", "/projects")
        assert r2["node"]["node_path"] == "/projects/release"
        assert r2["node"]["depth"] == 2
        view = tree_service.get_subtree(s, "/")
        assert [n["node_path"] for n in view["nodes"]] == [
            "/projects", "/projects/release"]


def test_add_node_rejects_dup_and_missing_parent(tree_env):
    session_factory, _ = tree_env
    with session_factory() as s:
        tree_service.add_node(s, "projects")
        with pytest.raises(ValueError, match="already exists"):
            tree_service.add_node(s, "projects")
        with pytest.raises(ValueError, match="parent node not found"):
            tree_service.add_node(s, "x", "/nope")


def test_assign_unassign_and_counts(tree_env):
    session_factory, _ = tree_env
    with session_factory() as s:
        tree_service.add_node(s, "projects")
        tree_service.add_node(s, "release", "/projects")
        _mem(s, "m1", "发布安排")
        _mem(s, "m2", "已归档旧事", status="archived")  # 归档不计 active 挂载
        s.commit()
        tree_service.assign_memory(s, "m1", "/projects/release")
        with pytest.raises(ValueError, match="node not found"):
            tree_service.assign_memory(s, "m1", "/projects/nope")
        with pytest.raises(ValueError, match="memory not found"):
            tree_service.assign_memory(s, "nope", "/projects")
        view = tree_service.get_subtree(s, "/projects")
        by_path = {n["node_path"]: n["attachments"] for n in view["nodes"]}
        assert by_path["/projects/release"]["direct"] == 1
        assert by_path["/projects"]["subtree"] == 1
        tree_service.unassign_memory(s, "m1")
        view = tree_service.get_subtree(s, "/")
        assert view["nodes"][0]["attachments"]["subtree"] == 0
