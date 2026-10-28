"""完璧（ADR-0057）多实体事实原子性存储层不 mock 冒烟测试。"""

import pytest
from sqlmodel import Session, create_engine, select

from lantai.models.tables import FactAtomic, FactEntity, SQLModel


@pytest.fixture
def mem_db():
    from sqlalchemy import event

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_fact_atomic_and_entity_projection_crud(mem_db):
    """验证 fact_atomic 与 fact_entity 基础 CRUD 及关联投影（真实 SQLite，不 mock）。"""
    with Session(mem_db) as session:
        fact = FactAtomic(
            id="fact_001",
            tenant_id="default",
            user_id="user_test",
            agent_id="agent_test",
            statement="张三、李四、王五于 2025 年共同创办了兰图科技",
            predicate="cofound",
            object="兰图科技",
            confidence=0.95,
            valid_from="2025-01-01",
            valid_to=None,
            source_memory_id="mem_123",
        )
        session.add(fact)
        session.commit()

        # 添加实体投影
        entities = [
            FactEntity(fact_id="fact_001", entity_name="张三", role="subject"),
            FactEntity(fact_id="fact_001", entity_name="李四", role="subject"),
            FactEntity(fact_id="fact_001", entity_name="王五", role="subject"),
            FactEntity(fact_id="fact_001", entity_name="兰图科技", role="object"),
        ]
        session.add_all(entities)
        session.commit()

    with Session(mem_db) as session:
        fetched_fact = session.get(FactAtomic, "fact_001")
        assert fetched_fact is not None
        assert fetched_fact.statement == "张三、李四、王五于 2025 年共同创办了兰图科技"
        assert fetched_fact.predicate == "cofound"

        # 通过实体反查事实 id
        stmt = select(FactEntity).where(FactEntity.entity_name == "李四")
        results = session.exec(stmt).all()
        assert len(results) == 1
        assert results[0].fact_id == "fact_001"
        assert results[0].role == "subject"

        # 验证所有投影实体均存在
        all_entities = session.exec(
            select(FactEntity).where(FactEntity.fact_id == "fact_001")
        ).all()
        assert len(all_entities) == 4
        names = {e.entity_name for e in all_entities}
        assert names == {"张三", "李四", "王五", "兰图科技"}


def test_fact_atomic_cascade_delete(mem_db):
    """验证删除 fact_atomic 记录时，SQLite 会正确级联删除对应的 fact_entity 投影行。"""
    with Session(mem_db) as session:
        fact = FactAtomic(
            id="fact_002",
            statement="Alice handed over Project X to Bob",
            predicate="handover",
            object="Project X",
        )
        session.add(fact)
        session.commit()

        session.add(FactEntity(fact_id="fact_002", entity_name="Alice", role="subject"))
        session.add(FactEntity(fact_id="fact_002", entity_name="Bob", role="recipient"))
        session.commit()

        # 确认投影存在
        entities_before = session.exec(
            select(FactEntity).where(FactEntity.fact_id == "fact_002")
        ).all()
        assert len(entities_before) == 2

        # 删除主表记录
        session.delete(fact)
        session.commit()

        # 验证投影被级联删除
        entities_after = session.exec(
            select(FactEntity).where(FactEntity.fact_id == "fact_002")
        ).all()
        assert len(entities_after) == 0


def test_migration_v31_upgrade():
    """验证从迁移链升级到 v31 能够正确建表并设置 user_version（不 mock）。"""
    import sqlite3

    from lantai.storage.migrations import apply_migrations

    conn = sqlite3.connect(":memory:")
    # 模拟 v30 状态
    conn.execute("PRAGMA user_version = 30")
    apply_migrations(conn)

    # 验证版本已递增至 31
    cur = conn.execute("PRAGMA user_version")
    assert cur.fetchone()[0] == 31

    # 验证表已生成
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    assert "fact_atomic" in tables
    assert "fact_entity" in tables
    conn.close()
