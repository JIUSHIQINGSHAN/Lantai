"""稽古（Jigu · ADR-0048）时态检索与历史快照不 mock 冒烟测试。

验证 as_of 参数在 hybrid_search 与 wanbi_retriever 之间的时态贯通：
- 在特定历史时点 as_of 查询时，仅返回在该时点生效且未失效/未被未来更正的事实；
- 未来产生的新事实绝不污染历史切片。
"""

from datetime import UTC
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryItem
from lantai.retrieval import hybrid
from lantai.retrieval.wanbi_retriever import search_atomic_facts_by_query
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def jigu_temporal_env(monkeypatch):
    """内存 SQLite 真实建表 + FTS + patch db.get_session。"""
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid.db, "get_session", session_factory)
    yield session_factory, engine


def test_jigu_as_of_wanbi_facts_query_smoke(jigu_temporal_env):
    """冒烟测试 1：search_atomic_facts_by_query 能够通过 as_of 过滤时间窗口。

    场景：
    - 历史事实 1 (2024 年)：张三担任星光科技 CTO (valid_from=2024-01-01, valid_to=2024-12-31)
    - 历史事实 2 (2025 年)：张三升任星光科技 CEO (valid_from=2025-01-01, valid_to=None)

    验证：
    - as_of="2024-06-01" -> 仅召回事实 1，绝无事实 2（防未来污染）
    - as_of="2025-06-01" -> 仅召回事实 2，事实 1 已过期
    """
    session_factory, _ = jigu_temporal_env

    fid_2024 = new_id("fact")
    fid_2025 = new_id("fact")

    with session_factory() as s:
        # 2024 年事实
        f1 = FactAtomic(
            id=fid_2024,
            statement="张三在2024年担任星光科技CTO",
            predicate="position",
            object="CTO",
            confidence=0.9,
            valid_from="2024-01-01T00:00:00+00:00",
            valid_to="2024-12-31T23:59:59+00:00",
        )
        # 2025 年事实
        f2 = FactAtomic(
            id=fid_2025,
            statement="张三在2025年升任星光科技CEO",
            predicate="position",
            object="CEO",
            confidence=0.95,
            valid_from="2025-01-01T00:00:00+00:00",
            valid_to=None,
        )
        s.add_all([f1, f2])
        s.add_all(
            [
                FactEntity(fact_id=fid_2024, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_2024, entity_name="星光科技", role="organization"),
                FactEntity(fact_id=fid_2025, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_2025, entity_name="星光科技", role="organization"),
            ]
        )
        s.commit()

    with session_factory() as s:
        # 查询 2024 年历史
        facts_2024 = search_atomic_facts_by_query(
            s, query="张三在星光科技担任什么职务？", as_of="2024-06-01T12:00:00+00:00"
        )
        assert len(facts_2024) == 1
        assert facts_2024[0]["id"] == fid_2024
        assert facts_2024[0]["object"] == "CTO"

        # 查询 2025 年当前
        facts_2025 = search_atomic_facts_by_query(
            s, query="张三在星光科技担任什么职务？", as_of="2025-06-01T12:00:00+00:00"
        )
        assert len(facts_2025) == 1
        assert facts_2025[0]["id"] == fid_2025
        assert facts_2025[0]["object"] == "CEO"


def test_jigu_hybrid_search_as_of_smoke(jigu_temporal_env):
    """冒烟测试 2：hybrid_search 传入 as_of，记忆和挂载的完璧原子事实均按 as_of 严格历史切片。"""
    session_factory, _ = jigu_temporal_env

    mid_old = new_id("mem")
    fid_old = new_id("fact")

    mid_new = new_id("mem")
    fid_new = new_id("fact")

    from datetime import datetime, timezone

    dt_2024_from = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    dt_2024_to = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)
    dt_2025_from = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    with session_factory() as s:
        # 旧记忆与事实 (2024)
        m_old = MemoryItem(
            id=mid_old,
            key="role_2024",
            content="2024年记录：张三担任星光科技CTO。",
            lane="general",
            status="active",
            importance=0.9,
            decay_score=1.0,
            valid_from=dt_2024_from,
            valid_to=dt_2024_to,
        )
        f_old = FactAtomic(
            id=fid_old,
            statement="张三在2024年担任星光科技CTO",
            predicate="position",
            object="CTO",
            confidence=0.9,
            valid_from="2024-01-01T00:00:00+00:00",
            valid_to="2024-12-31T23:59:59+00:00",
            source_memory_id=mid_old,
        )

        # 新记忆与事实 (2025)
        m_new = MemoryItem(
            id=mid_new,
            key="role_2025",
            content="2025年记录：张三升任星光科技CEO。",
            lane="general",
            status="active",
            importance=0.95,
            decay_score=1.0,
            valid_from=dt_2025_from,
            valid_to=None,
        )
        f_new = FactAtomic(
            id=fid_new,
            statement="张三在2025年升任星光科技CEO",
            predicate="position",
            object="CEO",
            confidence=0.95,
            valid_from="2025-01-01T00:00:00+00:00",
            valid_to=None,
            source_memory_id=mid_new,
        )

        s.add_all([m_old, f_old, m_new, f_new])
        s.add_all(
            [
                FactEntity(fact_id=fid_old, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_new, entity_name="张三", role="subject"),
            ]
        )
        s.commit()
        sync_fts(s, mid_old, m_old.content)
        sync_fts(s, mid_new, m_new.content)
        s.commit()

    with (
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "candidate_n": 5},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
        ),
    ):
        # 检索 2024-06-01 历史切片（严格模式）
        results_2024 = hybrid.hybrid_search(
            "张三在星光科技担任什么职务？",
            top_k=5,
            use_rerank=False,
            as_of="2024-06-01T12:00:00+00:00",
        )

        # 检索 2025-06-01 历史切片
        results_2025 = hybrid.hybrid_search(
            "张三在星光科技担任什么职务？",
            top_k=5,
            use_rerank=False,
            as_of="2025-06-01T12:00:00+00:00",
        )

    # 验证 1：2024 年检索结果只命中 2024 年记忆与事实，决不命中 2025 年未来事实
    assert len(results_2024) >= 1
    mids_2024 = [r["memory"]["id"] for r in results_2024]
    assert mid_old in mids_2024
    assert mid_new not in mids_2024  # 未来记忆不得出现

    for r in results_2024:
        if "atomic_facts" in r:
            fact_ids = [f["id"] for f in r["atomic_facts"]]
            assert fid_new not in fact_ids  # 决不污染未来事实
            assert fid_old in fact_ids

    # 验证 2：2025 年检索结果中 2024 年旧事实已失效退出
    assert len(results_2025) >= 1
    for r in results_2025:
        if "atomic_facts" in r:
            fact_ids = [f["id"] for f in r["atomic_facts"]]
            assert fid_old not in fact_ids
            assert fid_new in fact_ids
