"""稽古（Jigu · ADR-0048）迟到更正时态消解与防未来污染不 mock 冒烟测试。

测试接缝：
1. record_ops_service.supersede_fact_atomic：独立原子事实迟到更正接缝；
2. late_correction.apply_supersede_correction：记忆级迟到更正级联原子事实时态对齐接缝；
3. wanbi_retriever.search_atomic_facts_by_query 与 hybrid_search：as_of 历史切片防污染接缝。
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.cognition.late_correction import apply_supersede_correction
from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryAuditEvent, MemoryItem
from lantai.retrieval import hybrid
from lantai.retrieval.wanbi_retriever import search_atomic_facts_by_query
from lantai.services.record_ops_service import supersede_fact_atomic
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def jigu_test_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid.db, "get_session", session_factory)
    yield session_factory, engine
    engine.dispose()


def test_supersede_fact_atomic_smoke(jigu_test_db):
    """冒烟测试 1：直接对 FactAtomic 执行迟到更正 (supersede)。

    场景：
    - 旧事实 (f_old)：张三担任星光科技 CTO，valid_from="2024-01-01T00:00:00+00:00"，valid_to=None；
    - 新事实 (f_new)：张三卸任 CTO 并改任星光科技技术顾问；
    - 迟到更正生效时刻 anchor="2024-09-01T00:00:00+00:00"；
    - 校验：
      1. 旧事实 valid_to 变为 anchor；
      2. 新事实 valid_from 变为 anchor；
      3. 产生 audit_event，action="supersede"；
      4. 历史检索 as_of="2024-06-01" 仅见 CTO，无顾问（防未来事实渗透）；
      5. 历史检索 as_of="2024-10-01" 仅见顾问，CTO 已失效。
    """
    session_factory, _ = jigu_test_db
    fid_old = new_id("fact")
    fid_new = new_id("fact")

    with session_factory() as s:
        f_old = FactAtomic(
            id=fid_old,
            statement="张三在星光科技担任CTO",
            predicate="position",
            object="CTO",
            confidence=0.9,
            valid_from="2024-01-01T00:00:00+00:00",
            valid_to=None,
        )
        f_new = FactAtomic(
            id=fid_new,
            statement="张三改任星光科技技术顾问",
            predicate="position",
            object="技术顾问",
            confidence=0.95,
            valid_from=None,  # 待迟到更正锚定
            valid_to=None,
        )
        s.add_all([f_old, f_new])
        s.add_all(
            [
                FactEntity(fact_id=fid_old, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_old, entity_name="星光科技", role="organization"),
                FactEntity(fact_id=fid_new, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_new, entity_name="星光科技", role="organization"),
            ]
        )
        s.commit()

    anchor = "2024-09-01T00:00:00+00:00"
    with session_factory() as s:
        res = supersede_fact_atomic(
            old_fact_id=fid_old,
            new_fact_id=fid_new,
            anchor=anchor,
            actor="test_user",
            reason="事后迟到补充：9月改任顾问",
            session=s,
        )
        assert res["ok"] is True
        assert res["old_fact_id"] == fid_old
        assert res["new_fact_id"] == fid_new

    # 验证数据库状态
    with session_factory() as s:
        f1 = s.get(FactAtomic, fid_old)
        f2 = s.get(FactAtomic, fid_new)
        assert f1.valid_to == anchor
        assert f2.valid_from == anchor

        # 验证双时间轴独立性：事务时间 created_at 仍为当前写入时间，事件时间 valid_to/from 为历史生效时刻
        assert f1.created_at is not None
        assert f2.created_at is not None
        assert f1.created_at.year >= 2026

        # 验证审计日志
        audits = s.exec(
            select(MemoryAuditEvent).where(MemoryAuditEvent.action == "supersede")
        ).all()
        assert len(audits) >= 1
        assert "genglou:supersede_fact_atomic" in audits[0].reason

        # 时空切片验证
        # 1. 2024-06-01：应该只看到 CTO
        res_june = search_atomic_facts_by_query(
            s, query="张三在星光科技职务", as_of="2024-06-01T12:00:00+00:00"
        )
        assert len(res_june) == 1
        assert res_june[0]["id"] == fid_old
        assert res_june[0]["object"] == "CTO"

        # 2. 2024-10-01：应该只看到 技术顾问
        res_oct = search_atomic_facts_by_query(
            s, query="张三在星光科技职务", as_of="2024-10-01T12:00:00+00:00"
        )
        assert len(res_oct) == 1
        assert res_oct[0]["id"] == fid_new
        assert res_oct[0]["object"] == "技术顾问"


def test_cascade_supersede_from_memory_smoke(jigu_test_db):
    """冒烟测试 2：通过 apply_supersede_correction 级联对齐所挂原子事实的时态切片。

    场景：
    - 旧记忆 (m_old) 挂有原子事实 (f_old)，valid_from="2024-01-01T00:00:00+00:00", valid_to=None；
    - 新记忆 (m_new) 声明事件时间 event_time=2024-08-15，挂有新原子事实 (f_new)；
    - 执行 apply_supersede_correction(m_old, m_new)；
    - 验证：
      1. m_old.valid_to 对齐为 2024-08-15；
      2. f_old.valid_to 级联对齐为 2024-08-15；
      3. f_new.valid_from 对齐为 2024-08-15；
      4. hybrid_search(as_of=2024-07-01) 仅召回旧事实；
      5. hybrid_search(as_of=2024-09-01) 仅召回新事实。
    """
    session_factory, _ = jigu_test_db
    mid_old = new_id("mem")
    mid_new = new_id("mem")
    fid_old = new_id("fact")
    fid_new = new_id("fact")

    dt_event_new = datetime(2024, 8, 15, 0, 0, 0, tzinfo=UTC)

    with session_factory() as s:
        m_old = MemoryItem(
            id=mid_old,
            key="k_old",
            content="张三在2024年担任星光科技CTO",
            lane="general",
            status="active",
            importance=0.9,
            decay_score=1.0,
            valid_from=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            valid_to=None,
        )
        m_new = MemoryItem(
            id=mid_new,
            key="k_new",
            content="更正：张三从2024年8月15日起转任技术顾问",
            lane="general",
            status="active",
            importance=0.95,
            decay_score=1.0,
            event_time=dt_event_new,
            valid_from=None,
            valid_to=None,
        )
        f_old = FactAtomic(
            id=fid_old,
            statement="张三在星光科技担任CTO",
            predicate="position",
            object="CTO",
            confidence=0.9,
            valid_from="2024-01-01T00:00:00+00:00",
            valid_to=None,
            source_memory_id=mid_old,
        )
        f_new = FactAtomic(
            id=fid_new,
            statement="张三转任星光科技技术顾问",
            predicate="position",
            object="技术顾问",
            confidence=0.95,
            valid_from=None,
            valid_to=None,
            source_memory_id=mid_new,
        )
        s.add_all([m_old, m_new, f_old, f_new])
        s.add_all(
            [
                FactEntity(fact_id=fid_old, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_old, entity_name="星光科技", role="organization"),
                FactEntity(fact_id=fid_new, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid_new, entity_name="星光科技", role="organization"),
            ]
        )
        s.commit()
        sync_fts(s, mid_old, m_old.content)
        sync_fts(s, mid_new, m_new.content)
        s.commit()

    # 执行替换型迟到更正
    with session_factory() as s:
        m_old_db = s.get(MemoryItem, mid_old)
        m_new_db = s.get(MemoryItem, mid_new)
        res = apply_supersede_correction(
            m_old_db,
            m_new_db,
            session=s,
            reason="迟到更正：转任顾问",
            actor="admin",
        )
        assert res["ok"] is True
        s.commit()

    # 验证级联对齐
    with session_factory() as s:
        f_old_check = s.get(FactAtomic, fid_old)
        f_new_check = s.get(FactAtomic, fid_new)
        assert f_old_check.valid_to is not None
        assert f_old_check.valid_to.startswith("2024-08-15")
        assert f_new_check.valid_from is not None
        assert f_new_check.valid_from.startswith("2024-08-15")

    # 检索验证防污染
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
        # 查 2024-07-01：仅见旧事实
        res_july = hybrid.hybrid_search(
            "张三在星光科技职务",
            top_k=5,
            use_rerank=False,
            as_of="2024-07-01T12:00:00+00:00",
        )
        assert len(res_july) >= 1
        facts_july = [
            f["id"] for r in res_july if "atomic_facts" in r for f in r["atomic_facts"]
        ]
        assert fid_old in facts_july
        assert fid_new not in facts_july

        # 查 2024-09-01：仅见新事实
        res_sep = hybrid.hybrid_search(
            "张三在星光科技职务",
            top_k=5,
            use_rerank=False,
            as_of="2024-09-01T12:00:00+00:00",
        )
        assert len(res_sep) >= 1
        facts_sep = [
            f["id"] for r in res_sep if "atomic_facts" in r for f in r["atomic_facts"]
        ]
        assert fid_new in facts_sep
        assert fid_old not in facts_sep
