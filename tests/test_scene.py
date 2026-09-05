"""scene 聚合层测试（ADR-0012）：聚类/导航纯函数不 mock；
外部依赖（embedding、LLM 命名）按测试纪律允许 mock。"""
import importlib.util
import os
import sqlite3

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem, MemoryScene

HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "shell_hook.py")


def _item(i, use_count=0, importance=0.5):
    return MemoryItem(id=f"m{i}", memory_type="semantic", key=f"key{i}",
                      content=f"content{i}", lane="general", status="active",
                      use_count=use_count, importance=importance)


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（scene 全链路测试用）。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    from contextlib import contextmanager

    @contextmanager
    def _patch_session(session_factory):
        import lantai.storage.db as dbm
        original = dbm.get_session
        dbm.get_session = session_factory
        try:
            yield
        finally:
            dbm.get_session = original

    with _patch_session(session_factory):
        yield session_factory, engine


def _load_hook(monkeypatch):
    spec = importlib.util.spec_from_file_location("shell_hook_scene", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 0.2)
    return mod


# ── 纯函数：不 mock ─────────────────────────────────────────────


def test_cosine_sim_basic():
    from lantai.services.scene_service import cosine_sim
    assert cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_sim([], [1.0]) == 0.0
    assert cosine_sim([1.0], [1.0, 2.0]) == 0.0


def test_cluster_scenes_groups_similar():
    """纯函数冒烟：相近向量同簇，离群者开新簇；簇内顺序保持。"""
    from lantai.services.scene_service import cluster_scenes
    items = [_item(1), _item(2), _item(3)]
    vectors = [[1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.0, 0.0, 1.0]]
    clusters = cluster_scenes(items, vectors, 0.8)
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]
    for cluster in clusters:
        ids = [m.id for m in cluster]
        assert ids == sorted(ids, key=lambda x: int(x[1:]))  # 输入顺序保持


def test_incremental_cluster_pure():
    """纯函数冒烟：相似向量命中质心下标；离群/空质心返回 None 与相似度。"""
    from lantai.services.scene_service import incremental_cluster
    centroids = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    idx, sim = incremental_cluster([0.99, 0.05, 0.0], centroids, 0.8)
    assert idx == 0
    assert sim >= 0.8
    idx, sim = incremental_cluster([0.0, 0.0, 1.0], centroids, 0.8)
    assert idx is None
    assert sim < 0.8
    assert incremental_cluster([1.0, 0.0], [[1.0, 0.0]], 0.8) == (0, 1.0)
    assert incremental_cluster([], [], 0.8) == (None, -1.0)

def test_pick_representative_uses_use_count():
    from lantai.services.scene_service import pick_representative
    items = [_item(1, use_count=1, importance=0.2),
             _item(2, use_count=5, importance=0.1)]
    assert pick_representative(items).id == "m2"


def test_format_scene_block_header_members_and_budget():
    """纯函数冒烟：导航块含名称/热度/成员；超预算按码点截断附后缀。"""
    from lantai.services.scene_service import format_scene_block
    scene = {"name": "部署流程", "summary": "上线部署标准流程",
             "heat": 7, "member_count": 2}
    members = [_item(1), _item(2)]
    line, content = format_scene_block(scene, members, 500, "…suffix")
    assert line.startswith("## Scene: 部署流程（热度 7，成员 2）")
    assert "上线部署标准流程" in line
    assert "key1" in line and "key2" in line
    assert content == line  # evidence 与注入行同源
    long_scene = {"name": "长场景" * 50, "summary": "", "heat": 1, "member_count": 1}
    truncated, _ = format_scene_block(long_scene, [_item(1)], 30, "…suffix")
    assert truncated.endswith("…suffix")
    assert len(truncated) <= 30 + len("…suffix")


def test_scene_navigation_respects_total_budget():
    """纯函数冒烟：导航块超总预算丢弃剩余并计数。"""
    from lantai.services.scene_service import scene_navigation
    blocks = [("A" * 100, "A" * 100), ("B" * 100, "B" * 100)]
    lines, dropped = scene_navigation(blocks, 150)
    assert len(lines) == 1
    assert dropped == 1


# ── 迁移：真实临时 SQLite，不 mock ──────────────────────────────


def test_migration_v3_adds_scene_column_and_table(tmp_path):
    """v2 老库 → v3：scene_id 列补齐 + memoryscene 表创建 + 数据零丢失。"""
    from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE memoryitem ("
        "id TEXT PRIMARY KEY, content TEXT,"
        "lane TEXT DEFAULT 'general', status TEXT DEFAULT 'active',"
        "decay_score REAL DEFAULT 1.0, decay_class TEXT DEFAULT 'episodic'"
        ");"
        "CREATE TABLE retrieval_event (id TEXT PRIMARY KEY, query TEXT);"
        "CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, summary TEXT);"
    )
    conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m1', '老数据')")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    apply_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memoryitem)").fetchall()}
    assert "scene_id" in cols
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "memoryscene" in tables
    assert conn.execute(
        "SELECT content FROM memoryitem WHERE id='m1'").fetchone()[0] == "老数据"
    conn.close()


def test_migration_v5_adds_scene_centroid(tmp_path):
    """v4 老库 → v5：memoryscene 补 centroid 列 + 数据零丢失。"""
    from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations
    path = tmp_path / "v4.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE memoryitem ("
        "id TEXT PRIMARY KEY, content TEXT,"
        "lane TEXT DEFAULT 'general', status TEXT DEFAULT 'active',"
        "decay_score REAL DEFAULT 1.0, decay_class TEXT DEFAULT 'episodic',"
        "scene_id TEXT"
        ");"
        "CREATE TABLE memoryscene ("
        "id TEXT PRIMARY KEY, name TEXT, summary TEXT,"
        "heat INTEGER DEFAULT 0, member_count INTEGER DEFAULT 0"
        ");"
    )
    conn.execute("INSERT INTO memoryscene (id, name) VALUES ('s1', '旧场景')")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    apply_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memoryscene)").fetchall()}
    assert "centroid" in cols
    assert conn.execute(
        "SELECT name FROM memoryscene WHERE id='s1'").fetchone()[0] == "旧场景"
    conn.close()

# ── 写入侧：真实内存 SQLite，仅 mock 外部 embed/LLM ──────────────


def test_rebuild_scenes_clusters_and_assigns(mem_db, monkeypatch):
    """rebuild：聚类 → 命名降级代表 key → 落库 + scene_id 回写 + heat 求和。"""
    from unittest.mock import patch
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(_item(1, use_count=3))
        s.add(_item(2, use_count=1))
        s.add(_item(3, use_count=0))
        s.commit()
    with patch("lantai.llm.client.embed",
               return_value=[[1.0, 0.0], [0.99, 0.05], [0.0, 1.0]]), \
         patch("lantai.llm.client.chat_json",
               side_effect=RuntimeError("llm down")):
        from lantai.services.scene_service import rebuild_scenes
        result = rebuild_scenes(threshold=0.8)
    assert result["ok"] is True
    assert result["scene_count"] == 1
    assert result["member_count"] == 2  # 单成员簇不建场景
    with session_factory() as s:
        scenes = s.exec(select(MemoryScene)).all()
        assert len(scenes) == 1
        sc = scenes[0]
        assert sc.heat == 4  # use_count 求和：3 + 1
        assert sc.member_count == 2
        assert sc.name == "key1"  # LLM 失败 → 代表 key 兜底
        assert sc.centroid == pytest.approx([0.995, 0.025])  # 质心落库（增量聚类基础）
        members = s.exec(select(MemoryItem).where(MemoryItem.scene_id == sc.id)).all()
        assert len(members) == 2
        assert s.get(MemoryItem, "m3").scene_id is None  # 单成员簇不强制归属


def test_rebuild_scenes_empty_clears_old(mem_db, monkeypatch):
    """rebuild 幂等：无记忆时清空旧场景并返回 0。"""
    from unittest.mock import patch
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="old_scene", name="旧", summary="", heat=1,
                          member_count=1))
        s.commit()
    with patch("lantai.llm.client.embed", return_value=[]):
        from lantai.services.scene_service import rebuild_scenes
        result = rebuild_scenes()
    assert result == {"ok": True, "scene_count": 0, "member_count": 0}
    with session_factory() as s:
        assert s.exec(select(MemoryScene)).all() == []


def test_assign_new_memory_hits_and_refreshes_heat(mem_db, monkeypatch):
    """assign_new_memory：命中场景 → 写 scene_id + 刷 heat/member_count（零写放大）。"""
    from unittest.mock import patch
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_x", name="部署", summary="", heat=4,
                          member_count=2, centroid=[1.0, 0.0, 0.0]))
        m1 = _item(1, use_count=3)
        m2 = _item(2, use_count=1)
        m1.scene_id = "scene_x"
        m2.scene_id = "scene_x"
        s.add(m1)
        s.add(m2)
        s.add(_item(9, use_count=2))  # 无 scene_id 的新记忆
        s.commit()
    from lantai.services.scene_service import assign_new_memory
    with patch("lantai.llm.client.embed", return_value=[[0.98, 0.1, 0.0]]):
        result = assign_new_memory("m9")
    assert result["assigned"] is True
    assert result["scene_id"] == "scene_x"
    with session_factory() as s:
        sc = s.get(MemoryScene, "scene_x")
        assert sc.heat == 6  # 3 + 1 + 2
        assert sc.member_count == 3
        assert s.get(MemoryItem, "m9").scene_id == "scene_x"


def test_assign_new_memory_miss_keeps_flat(mem_db, monkeypatch):
    """assign_new_memory：未命中阈值 → scene_id 保持 None、场景热值不脏写。"""
    from unittest.mock import patch
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_x", name="部署", summary="", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(_item(9, use_count=2))
        s.commit()
    from lantai.services.scene_service import assign_new_memory
    with patch("lantai.llm.client.embed", return_value=[[0.0, 1.0, 0.0]]):
        result = assign_new_memory("m9", threshold=0.8)
    assert result["assigned"] is False
    assert result["scene_id"] is None
    with session_factory() as s:
        assert s.get(MemoryItem, "m9").scene_id is None
        assert s.get(MemoryScene, "scene_x").member_count == 1


def test_assign_unassigned_scans_only_unassigned(mem_db, monkeypatch):
    """assign_unassigned：只扫 scene_id 为空的 active 记忆，命中并入、未命中保持平铺。"""
    from unittest.mock import patch
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_x", name="部署", summary="", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(_item(9, use_count=2))  # 无归属 → 命中
        s.add(_item(8, use_count=5))  # 无归属 → 离群未命中
        m7 = _item(7, use_count=1)
        m7.scene_id = "scene_x"
        s.add(m7)  # 已有归属 → 不重复扫描
        s.commit()

    def fake_embed(texts):
        return [[0.99, 0.0, 0.0] if "content9" in texts[0] else [0.0, 1.0, 0.0]
                for _ in texts]

    from lantai.services.scene_service import assign_unassigned
    with patch("lantai.llm.client.embed", side_effect=fake_embed):
        result = assign_unassigned(limit=50, threshold=0.8)
    assert result["scanned"] == 2  # m7 不重复扫描
    assert result["assigned"] == 1
    assert result["missed"] == 1
    with session_factory() as s:
        assert s.get(MemoryItem, "m9").scene_id == "scene_x"
        assert s.get(MemoryItem, "m8").scene_id is None
        assert s.get(MemoryScene, "scene_x").member_count == 2  # m7 + m9

def test_get_scene_and_list(mem_db, monkeypatch):
    """读取侧：get_scene 下钻成员（use_count 降序）；list_scenes 按 heat 排序。"""
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_a", name="甲", summary="摘要", heat=5,
                          member_count=2))
        m1 = _item(1, use_count=3)
        m2 = _item(2, use_count=1)
        m1.scene_id = "scene_a"
        m2.scene_id = "scene_a"
        s.add(m1)
        s.add(m2)
        s.commit()
    from lantai.services.scene_service import get_scene, list_scenes
    out = get_scene("scene_a")
    assert out["scene"]["name"] == "甲"
    assert [m["id"] for m in out["members"]] == ["m1", "m2"]
    with pytest.raises(ValueError):
        get_scene("scene_missing")
    listing = list_scenes()
    assert listing["scenes"][0]["id"] == "scene_a"
    with pytest.raises(ValueError):
        list_scenes(limit=0)


# ── 读取侧：shell_hook 场景导航注入 ──────────────────────────────


def _fake_store(ids):
    class _FakeStore:
        def search(self, qv, top_k=5):
            return [{"id": i, "distance": 0.1 + 0.1 * n}
                    for n, i in enumerate(ids)]
    return _FakeStore()


def test_build_context_injects_scene_navigation(mem_db, monkeypatch):
    """SCENE_LAYER_ENABLED 开启且命中场景成员 → 注入 ## Scene 导航块。"""
    mod = _load_hook(monkeypatch)
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_b", name="部署流程", summary="上线步骤",
                          heat=4, member_count=2))
        m1 = _item(1, use_count=3)
        m2 = _item(2, use_count=1)
        m1.scene_id = "scene_b"
        m2.scene_id = "scene_b"
        s.add(m1)
        s.add(m2)
        s.commit()
    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(mod, "get_vector_store", lambda: _fake_store(["m1", "m2"]))
    monkeypatch.setattr(mod, "embed", lambda texts: [[0.1] * 8])
    monkeypatch.setattr(mod.settings, "SCENE_LAYER_ENABLED", True)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_CHARS_PER_SCENE", 500)
    out = mod.build_context("部署怎么做")
    assert "## Scene: 部署流程" in out["context"]
    assert "热度 4" in out["context"]
    assert "key1" in out["context"]
    assert out["evidence"][0]["id"] == "m1"


def test_build_context_scene_disabled_plain(mem_db, monkeypatch):
    """默认关闭：无 ## Scene 块，行为与平铺注入一致。"""
    mod = _load_hook(monkeypatch)
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_c", name="部署", summary="", heat=1,
                          member_count=1))
        m1 = _item(1)
        m1.scene_id = "scene_c"
        s.add(m1)
        s.commit()
    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(mod, "get_vector_store", lambda: _fake_store(["m1"]))
    monkeypatch.setattr(mod, "embed", lambda texts: [[0.1] * 8])
    out = mod.build_context("部署怎么做")
    assert "## Scene:" not in out["context"]
    assert "content1" in out["context"]
