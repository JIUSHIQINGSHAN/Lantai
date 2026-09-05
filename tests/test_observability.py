"""可观测性测试（零召回监控 + token 估算 + scene 埋点）：
estimate_tokens / 迁移纯函数不 mock；log_retrieval / recall_report 用真实内存 SQLite。"""
import sqlite3

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.models.tables import RetrievalEvent


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（可观测性全链路测试用）。"""
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


def _result(memory_id, content, scene_id=None):
    mem = {"id": memory_id, "content": content}
    if scene_id:
        mem["scene_id"] = scene_id
    return {"score": 0.9, "memory": mem}


# ── 纯函数：不 mock ─────────────────────────────────────────────


def test_estimate_tokens_cjk_and_ascii():
    from lantai.observability.recall_report import estimate_tokens
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4  # 4 个 CJK 字
    assert estimate_tokens("abcdefgh") == 2  # 8 字符 / 4
    assert estimate_tokens("你好 abcdefgh") == 2 + 2
    assert estimate_tokens("m" * 3) == 0  # 3 字符不足 4 → 0


def test_migration_v4_adds_observability_columns(tmp_path):
    """v3 老库 → v4：retrieval_event 补 scene_ids / estimated_tokens + 数据零丢失。"""
    from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE memoryitem ("
        "id TEXT PRIMARY KEY, content TEXT,"
        "lane TEXT DEFAULT 'general', status TEXT DEFAULT 'active',"
        "decay_score REAL DEFAULT 1.0, decay_class TEXT DEFAULT 'episodic',"
        "scene_id TEXT"
        ");"
        "CREATE TABLE retrieval_event ("
        "id TEXT PRIMARY KEY, query_text TEXT, zero_result BOOLEAN DEFAULT 0,"
        "is_system_noise BOOLEAN DEFAULT 0, latency_ms INTEGER DEFAULT 0"
        ");"
        "CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, summary TEXT);"
        "CREATE TABLE memoryscene ("
        "id TEXT PRIMARY KEY, name TEXT, heat INTEGER DEFAULT 0"
        ");"
    )
    conn.execute("INSERT INTO retrieval_event (id, query_text) VALUES ('r1', '老事件')")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    apply_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(retrieval_event)").fetchall()}
    assert "scene_ids" in cols and "estimated_tokens" in cols
    assert conn.execute(
        "SELECT query_text FROM retrieval_event WHERE id='r1'").fetchone()[0] == "老事件"
    conn.close()


# ── log_retrieval 埋点：scene_ids / estimated_tokens ─────────────


def test_log_retrieval_records_scene_and_tokens(mem_db, monkeypatch):
    """埋点：命中记忆的 scene_id 去重落库 + query/结果 token 估算。"""
    from lantai.observability.retrieval_log import log_retrieval
    session_factory, _ = mem_db
    results = [_result("m1", "你好世界部署", scene_id="sc_a"),
               _result("m2", "abcdefgh", scene_id="sc_a"),
               _result("m3", "无场景内容", scene_id=None)]
    event_id = log_retrieval("查部署方案", results, latency_ms=12,
                             gate={"intent": "factual"}, trace_id="test")
    assert event_id is not None
    with session_factory() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev.scene_ids == ["sc_a"]  # 去重排序
        assert ev.estimated_tokens == 5 + 6 + 2 + 5  # query 5 CJK + 结果 6/2/5
        assert ev.zero_result is False
        assert ev.is_system_noise is False


def test_log_retrieval_zero_and_noise(mem_db, monkeypatch):
    """埋点：零结果 + 系统噪音标记。"""
    from lantai.observability.retrieval_log import log_retrieval
    session_factory, _ = mem_db
    event_id = log_retrieval("review the conversation above and save", [], latency_ms=3)
    with session_factory() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev.zero_result is True
        assert ev.is_system_noise is True
        assert ev.estimated_tokens == 9  # 38 字符 / 4 = 9（非 CJK）

def test_recall_report_aggregates(mem_db, monkeypatch):
    """报告聚合（核心函数不 mock）：排除噪音、按 lane/intent 分组、场景命中、token 汇总。"""
    from lantai.observability.retrieval_log import log_retrieval
    session_factory, _ = mem_db
    log_retrieval("查部署方案", [_result("m1", "你好世界部署", scene_id="sc_a")],
                  latency_ms=5, gate={"intent": "factual"}, lanes=["general"])
    log_retrieval("独有名词不存在", [], latency_ms=3,
                  gate={"intent": "entity"}, lanes=["general"])
    log_retrieval("review the conversation above and save", [], latency_ms=2)
    from lantai.observability.recall_report import recall_report
    monkeypatch.setattr("lantai.core.settings.settings.SCENE_LAYER_ENABLED", True)
    rep = recall_report(days=7)
    assert rep["total"] == 3
    assert rep["system_noise"] == 1
    assert rep["real"] == 2
    assert rep["zero"] == 1
    assert rep["zero_recall_rate"] == pytest.approx(0.5)
    assert rep["by_lane"]["general"]["total"] == 2
    assert rep["by_lane"]["general"]["zero"] == 1
    assert rep["by_intent"]["factual"]["total"] == 1
    assert rep["by_intent"]["entity"]["zero"] == 1
    assert rep["scene"]["enabled"] is True
    assert rep["scene"]["events"] == 2
    assert rep["scene"]["hit"] == 1
    assert rep["scene"]["hit_rate"] == pytest.approx(0.5)
    assert rep["estimated_tokens"]["total"] == (5 + 6) + 7  # 事件1 query+结果，事件2 query
    assert rep["estimated_tokens"]["avg_per_query"] == pytest.approx(9.0)
