"""完璧（ADR-0057）实体检索与投影查询不 mock 冒烟测试。"""

import pytest
from sqlmodel import Session, create_engine

from lantai.models.tables import FactAtomic, FactEntity, SQLModel
from lantai.retrieval.wanbi_retriever import get_facts_by_entity, get_facts_by_predicate


@pytest.fixture
def mem_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_get_facts_by_entity_full_projection(mem_db):
    """测试按单一实体反查时，返回完整的事实句及同事实下的全部实体。"""
    with Session(mem_db) as session:
        # 写入一条三元共同创办的事实
        fact = FactAtomic(
            id="fact_founder",
            statement="张三、李四、王五于 2025 年共同创办了兰图科技",
            predicate="cofound",
            object="兰图科技",
            confidence=0.9,
            user_id="user_1",
        )
        session.add(fact)
        session.add_all(
            [
                FactEntity(fact_id="fact_founder", entity_name="张三", role="subject"),
                FactEntity(fact_id="fact_founder", entity_name="李四", role="subject"),
                FactEntity(fact_id="fact_founder", entity_name="王五", role="subject"),
                FactEntity(fact_id="fact_founder", entity_name="兰图科技", role="object"),
            ]
        )
        session.commit()

    with Session(mem_db) as session:
        # 查询李四
        results = get_facts_by_entity(session, "李四")
        assert len(results) == 1
        f = results[0]
        assert f["id"] == "fact_founder"
        assert f["statement"] == "张三、李四、王五于 2025 年共同创办了兰图科技"
        assert f["predicate"] == "cofound"

        # 验证关联实体列表包含全部 4 个实体
        entities = f["entities"]
        assert len(entities) == 4
        names = {e["name"] for e in entities}
        assert names == {"张三", "李四", "王五", "兰图科技"}


def test_get_facts_by_predicate(mem_db):
    """测试按谓词查询原子事实。"""
    with Session(mem_db) as session:
        fact1 = FactAtomic(
            id="f1",
            statement="Alice signed Protocol Alpha with Bob",
            predicate="sign",
            object="Protocol Alpha",
        )
        fact2 = FactAtomic(
            id="f2",
            statement="Charlie co-founded Beta Corp with David",
            predicate="cofound",
            object="Beta Corp",
        )
        session.add_all([fact1, fact2])
        session.add_all(
            [
                FactEntity(fact_id="f1", entity_name="Alice", role="subject"),
                FactEntity(fact_id="f1", entity_name="Bob", role="subject"),
                FactEntity(fact_id="f2", entity_name="Charlie", role="subject"),
            ]
        )
        session.commit()

    with Session(mem_db) as session:
        res = get_facts_by_predicate(session, "sign")
        assert len(res) == 1
        assert res[0]["id"] == "f1"
        assert len(res[0]["entities"]) == 2

        res_none = get_facts_by_predicate(session, "non_existent")
        assert res_none == []
