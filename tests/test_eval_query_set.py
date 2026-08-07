"""评估查询集冒烟测试——真实 SQLite，不 mock。

覆盖：表可建、build_query_set 过滤噪音/去重 norm_hash/limit、load_query_set、同名覆盖。
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import remembrance.storage.db as db_module
from remembrance.models.tables import RetrievalEvent
from remembrance.eval.models import EvalQuerySet, EvalRun
from remembrance.eval.query_set import build_query_set, load_query_set


@pytest.fixture(scope="function")
def db_session():
    """内存 SQLite + patch db.get_session。"""
    import remembrance.models.tables  # noqa: F401
    import remembrance.eval.models  # noqa: F401
    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        db_module, "get_session", get_test_session
    ):
        yield get_test_session


def _add_event(s, eid, query, norm_hash, noise=False, lane="", created_at=None):
    s.add(RetrievalEvent(
        id=eid, trace_id="t", query_text=query, query_norm_hash=norm_hash,
        lane=lane, param_snapshot_hash="sha256:x",
        result_ids=[], result_scores=[], used_ids=[],
        latency_ms=5, zero_result=False, is_system_noise=noise,
        created_at=created_at or __import__("datetime").datetime(2026, 8, 1),
    ))
    s.commit()


class TestEvalTables:
    def test_tables_created(self, db_session):
        """EvalQuerySet / EvalRun 表可建可写。"""
        sf = db_session
        with sf() as s:
            qs = EvalQuerySet(id="eqs_test", name="v1", sample_count=3,
                              queries=[{"query": "hi"}])
            run = EvalRun(id="erun_test", query_set_id="eqs_test",
                          query_set_name="v1", status="done")
            s.add(qs); s.add(run); s.commit()
            assert s.get(EvalQuerySet, "eqs_test").name == "v1"
            assert s.get(EvalRun, "erun_test").status == "done"


class TestBuildQuerySet:
    def test_filters_noise_by_default(self, db_session):
        """默认排除 is_system_noise=1。"""
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "real query A", "hash_a", noise=False)
            _add_event(s, "e2", "system noise", "hash_b", noise=True)
        qs = build_query_set("v1")
        assert qs.sample_count == 1
        assert qs.queries[0]["query"] == "real query A"

    def test_dedup_by_norm_hash(self, db_session):
        """同 norm_hash 去重，保留最新。"""
        import datetime
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "old query", "dup_hash",
                       created_at=datetime.datetime(2026, 8, 1))
            _add_event(s, "e2", "new query", "dup_hash",
                       created_at=datetime.datetime(2026, 8, 5))
        qs = build_query_set("dedup_test")
        assert qs.sample_count == 1  # 去重后 1 条
        assert qs.queries[0]["query"] == "new query"  # 保留最新

    def test_dedup_disabled(self, db_session):
        """dedup=False 保留重复。"""
        import datetime
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "old", "dup", created_at=datetime.datetime(2026, 8, 1))
            _add_event(s, "e2", "new", "dup", created_at=datetime.datetime(2026, 8, 5))
        qs = build_query_set("no_dedup", dedup=False)
        assert qs.sample_count == 2

    def test_limit(self, db_session):
        """limit 截断。"""
        sf = db_session
        with sf() as s:
            for i in range(5):
                _add_event(s, f"e{i}", f"q{i}", f"h{i}")
        qs = build_query_set("lim", limit=3)
        assert qs.sample_count == 3

    def test_criteria_recorded(self, db_session):
        """criteria JSON 记录构造参数。"""
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "q", "h")
        qs = build_query_set("crit", noise_excluded=True, dedup=True, limit=10)
        assert qs.criteria["noise_excluded"] is True
        assert qs.criteria["dedup"] is True
        assert qs.criteria["source"] == "retrieval_event"
        assert qs.criteria["limit"] == 10

    def test_same_name_overwrites(self, db_session):
        """同名查询集覆盖旧的。"""
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "first", "h1")
        build_query_set("overwrite")
        with sf() as s:
            _add_event(s, "e2", "second", "h2")
        qs = build_query_set("overwrite")  # 第二次
        assert qs.sample_count == 2
        # 数据库应只有 1 个同名查询集
        with sf() as s:
            rows = s.exec(select(EvalQuerySet).where(EvalQuerySet.name == "overwrite")).all()
            assert len(rows) == 1

    def test_query_fields(self, db_session):
        """queries 条目含全部契约字段。"""
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "real query", "hash_a", lane="fact")
        qs = build_query_set("fields")
        q = qs.queries[0]
        assert set(q.keys()) == {"query", "event_id", "lane", "norm_hash"}
        assert q["lane"] == "fact"
        assert q["event_id"] == "e1"
        assert q["norm_hash"] == "hash_a"


class TestLoadQuerySet:
    def test_load_existing(self, db_session):
        sf = db_session
        with sf() as s:
            _add_event(s, "e1", "q", "h")
        build_query_set("loadable")
        qs = load_query_set("loadable")
        assert qs is not None
        assert qs.name == "loadable"

    def test_load_missing_returns_none(self, db_session):
        assert load_query_set("nonexistent_xyz") is None
