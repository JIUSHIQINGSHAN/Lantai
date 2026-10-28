"""完璧（ADR-0057）与笔削（ADR-0047）生命周期操作不 mock 冒烟测试。"""

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryAuditEvent
from lantai.services.record_ops_service import delete_fact_atomic, retract_fact_atomic


@pytest.fixture
def bixiao_wanbi_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_retract_fact_atomic_disables_projection(bixiao_wanbi_db):
    """测试撤回（retract）原子事实：清空实体投影索引，记录主表失效并写审计。"""
    fid = new_id("fact")
    with Session(bixiao_wanbi_db) as s:
        fact = FactAtomic(
            id=fid,
            statement="张三、李四共同签署了保密协议",
            predicate="sign",
            object="保密协议",
            confidence=0.9,
        )
        s.add(fact)
        s.add_all(
            [
                FactEntity(fact_id=fid, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid, entity_name="李四", role="subject"),
            ]
        )
        s.commit()

    # 1. 执行撤回
    with Session(bixiao_wanbi_db) as s:
        res = retract_fact_atomic(fid, reason="协议已作废解除", actor="test_user", session=s)
        assert res["ok"] is True
        assert res["entities_cleared"] == 2

    # 2. 验证投影清空，实体反查 0 命中
    with Session(bixiao_wanbi_db) as s:
        f = s.get(FactAtomic, fid)
        assert f.confidence == 0.0
        assert f.valid_to is not None
        ents = s.exec(select(FactEntity).where(FactEntity.fact_id == fid)).all()
        assert ents == []

        # 验证审计事件记录
        audit = s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == fid)).all()
        assert len(audit) == 1
        assert audit[0].action == "retract"
        assert "wanbi:retract_fact_atomic" in audit[0].reason


def test_delete_fact_atomic_cascades(bixiao_wanbi_db):
    """测试删除（delete）原子事实：主表物理删除，实体投影级联清除，并写入无正文审计。"""
    fid = new_id("fact")
    with Session(bixiao_wanbi_db) as s:
        fact = FactAtomic(
            id=fid,
            statement="临时测试事实句",
            predicate="test",
            confidence=0.8,
        )
        s.add(fact)
        s.add(FactEntity(fact_id=fid, entity_name="测试者", role="subject"))
        s.commit()

    # 执行物理删除
    with Session(bixiao_wanbi_db) as s:
        res = delete_fact_atomic(fid, actor="test_admin", reason="物理彻底清除", session=s)
        assert res["ok"] is True

    # 验证主表与从表均彻底删除
    with Session(bixiao_wanbi_db) as s:
        assert s.get(FactAtomic, fid) is None
        assert s.exec(select(FactEntity).where(FactEntity.fact_id == fid)).all() == []
        audit = s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == fid)).all()
        assert len(audit) == 1
        assert audit[0].action == "delete"


def test_correct_fact_atomic_in_place(bixiao_wanbi_db):
    """测试纠错（correct）原子事实：就地改写 statement、全量替换投影表并留痕审计。"""
    from lantai.services.record_ops_service import correct_fact_atomic

    fid = new_id("fact")
    with Session(bixiao_wanbi_db) as s:
        fact = FactAtomic(
            id=fid,
            statement="张三与李四于 2024 年创办甲公司",
            predicate="cofound",
            confidence=0.9,
        )
        s.add(fact)
        s.add_all(
            [
                FactEntity(fact_id=fid, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid, entity_name="李四", role="subject"),
            ]
        )
        s.commit()

    # 执行纠错：修正为张三、李四和王五，2025 年创办
    with Session(bixiao_wanbi_db) as s:
        res = correct_fact_atomic(
            fid,
            new_statement="张三、李四与王五于 2025 年创办甲公司",
            entities=[("张三", "subject"), ("李四", "subject"), ("王五", "subject")],
            actor="editor",
            reason="补全联合创始人王五及年份更正",
            session=s,
        )
        assert res["ok"] is True

    # 验证主表更新与投影重建
    with Session(bixiao_wanbi_db) as s:
        f = s.get(FactAtomic, fid)
        assert f.statement == "张三、李四与王五于 2025 年创办甲公司"
        ents = s.exec(select(FactEntity).where(FactEntity.fact_id == fid)).all()
        assert len(ents) == 3
        assert {e.entity_name for e in ents} == {"张三", "李四", "王五"}

        # 验证审计
        audit = s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == fid)).all()
        assert len(audit) == 1
        assert audit[0].action == "correct"
        assert "wanbi:correct_fact_atomic" in audit[0].reason


def test_retract_memory_cascades_to_facts(bixiao_wanbi_db):
    """测试撤回来源记忆（retract_memory）时联动下线挂载的 FactAtomic。"""
    from lantai.models.tables import MemoryItem
    from lantai.services.record_ops_service import retract_memory

    mid = new_id("mem")
    fid = new_id("fact")
    with Session(bixiao_wanbi_db) as s:
        mem = MemoryItem(
            id=mid,
            key="test_cascade",
            content="这是一段测试记忆正文",
            lane="general",
            status="active",
        )
        fact = FactAtomic(
            id=fid,
            statement="测试记忆衍生出的原子事实",
            predicate="derive",
            confidence=0.85,
            source_memory_id=mid,
        )
        s.add_all([mem, fact])
        s.add(FactEntity(fact_id=fid, entity_name="测试实体", role="subject"))
        s.commit()

    # 撤回记忆
    with Session(bixiao_wanbi_db) as s:
        res = retract_memory(mid, reason="记忆废弃联动撤回", actor="tester", session=s)
        assert res["ok"] is True

    # 验证 FactAtomic 置信度置 0，投影被清空
    with Session(bixiao_wanbi_db) as s:
        f = s.get(FactAtomic, fid)
        assert f.confidence == 0.0
        assert f.valid_to is not None
        ents = s.exec(select(FactEntity).where(FactEntity.fact_id == fid)).all()
        assert ents == []
