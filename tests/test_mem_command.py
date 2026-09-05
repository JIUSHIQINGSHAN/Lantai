"""mem: 会话指令测试（借鉴 TencentDB Agent Memory mem-command）。

mem_help 纯函数不 mock；create_skill / mem_sync 用真实内存 SQLite（真实落库/幂等/
校验），仅 mock 外部依赖（embedding、向量存储、digest 输出目录文件副作用）。
"""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.models.tables import MemoryItem, MemoryScene


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（mem 命令全链路测试用）。"""
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


class _FakeVectorStore:
    """向量存储桩：记录 add 调用，不触外部 Chroma。"""

    def __init__(self):
        self.added = []

    def add(self, ids=None, embeddings=None, metadatas=None):
        self.added.append({"ids": ids, "metadatas": metadatas})


# ── mem_help：纯函数不 mock ────────────────────────────────────


def test_mem_help_pure():
    """纯函数冒烟：返回命令表 + 示例，含三个命令。"""
    from lantai.services.mem_command import mem_help
    out = mem_help()
    assert out["ok"] is True
    assert out["command"] == "mem:help"
    for cmd in ("mem_help", "mem_sync", "mem_create_skill"):
        assert cmd in out["text"]


# ── create_skill：真实 SQLite，仅 mock embed/向量库 ─────────────


def test_create_skill_persists_and_dedups(mem_db, monkeypatch):
    """create_skill：落库为 skill 资产（procedural + structure.steps）并可召回；重复幂等。"""
    session_factory, _ = mem_db
    fake_store = _FakeVectorStore()
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store",
                        lambda: fake_store)
    from lantai.services.mem_command import create_skill
    with patch("lantai.llm.client.embed", return_value=[[0.1] * 8]):
        r1 = create_skill(name="数据库迁移", description="迁移步骤与踩坑",
                          steps=["备份数据库", "执行迁移", "验证结果"], tags=["db"])
        r2 = create_skill(name="数据库迁移", description="迁移步骤与踩坑",
                          steps=["备份数据库", "执行迁移", "验证结果"])
    assert r1["ok"] is True and r1["dedup"] is False
    assert r2["ok"] is True and r2["dedup"] is True
    assert r1["memory_id"] == r2["memory_id"]
    with session_factory() as s:
        mem = s.get(MemoryItem, r1["memory_id"])
        assert mem is not None
        assert mem.memory_type == "skill"
        assert mem.decay_class == "procedural"  # 永不衰减
        assert mem.structure["name"] == "数据库迁移"
        assert mem.structure["steps"] == ["备份数据库", "执行迁移", "验证结果"]
        assert mem.content.startswith("数据库迁移")
    assert len(fake_store.added) == 1  # 向量索引只写一次（幂等去重）


def test_create_skill_validation_rejects_bad_input(mem_db, monkeypatch):
    """create_skill：name/steps 非法不落库（宁 miss 不脏写）。"""
    session_factory, _ = mem_db
    from lantai.services.mem_command import create_skill
    assert create_skill(name="", steps=["x"])["ok"] is False
    assert create_skill(name="   ", steps=["x"])["ok"] is False
    assert create_skill(name="名称", steps=[])["ok"] is False
    assert create_skill(name="名称", steps=["  "])["ok"] is False
    with session_factory() as s:
        assert s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "skill")).all() == []


# ── mem_sync：真实 SQLite，mock embed + digest 输出目录 ─────────


def test_mem_sync_runs_scene_and_digest(mem_db, monkeypatch, tmp_path):
    """mem_sync：scene 增量聚类补跑 + 今日 digest 重算落盘，返回耗时。"""
    import lantai.services.mem_command as mc
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="scene_x", name="部署", summary="", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(MemoryItem(id="m9", memory_type="semantic", key="key9",
                         content="content9", lane="general", status="active",
                         use_count=2))
        s.commit()
    monkeypatch.setattr(mc.settings, "SCENE_LAYER_ENABLED", True)
    monkeypatch.setattr("lantai.workers.digest_worker.settings.DIGEST_OUTPUT_DIR",
                        str(tmp_path))
    monkeypatch.setattr(mc.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(mc.settings, "WIKI_OVERVIEW_LLM", False)
    from lantai.services.mem_command import mem_sync
    with patch("lantai.llm.client.embed", return_value=[[0.99, 0.0, 0.0]]):
        out = mem_sync()
    assert out["ok"] is True
    assert out["command"] == "mem:sync"
    assert out["scene"]["assigned"] == 1
    assert out["digest"]["ok"] is True
    assert out["wiki"]["ok"] is True
    assert out["took_ms"] >= 0
    with session_factory() as s:
        assert s.get(MemoryItem, "m9").scene_id == "scene_x"
    assert any(tmp_path.glob("*.md"))  # digest 报告已落盘


def test_mem_sync_scene_disabled_skips_scene(mem_db, monkeypatch, tmp_path):
    """mem_sync：SCENE_LAYER_ENABLED=false 时 scene 部分跳过，digest 照常。"""
    import lantai.services.mem_command as mc
    session_factory, _ = mem_db
    monkeypatch.setattr(mc.settings, "SCENE_LAYER_ENABLED", False)
    monkeypatch.setattr("lantai.workers.digest_worker.settings.DIGEST_OUTPUT_DIR",
                        str(tmp_path))
    monkeypatch.setattr(mc.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(mc.settings, "WIKI_OVERVIEW_LLM", False)
    from lantai.services.mem_command import mem_sync
    out = mem_sync()
    assert out["ok"] is True
    assert out["scene"]["skipped"]
    assert out["digest"]["ok"] is True
    assert out["wiki"]["ok"] is True