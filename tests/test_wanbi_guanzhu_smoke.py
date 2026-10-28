"""贯珠（ADR-0035）结合完璧（ADR-0057）多实体图谱扩散不 mock 冒烟测试。"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryEdge, MemoryItem
from lantai.retrieval.graph_retriever import expand_graph_associations, graph_augmented_search


@pytest.fixture
def graph_wanbi_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_expand_graph_associations_with_wanbi(graph_wanbi_db):
    """不 mock 核心逻辑冒烟：验证种子记忆展开时，能通过完璧原子事实扩散出多实体关系。"""
    sid = new_id("mem")
    fid = new_id("fact")

    with Session(graph_wanbi_db) as s:
        # 种子记忆
        mem = MemoryItem(
            id=sid,
            key="seed_key",
            content="关于兰图科技创始人团队的事实记录",
            lane="general",
            status="active",
        )
        # 关联的原子事实
        fact = FactAtomic(
            id=fid,
            statement="张三、李四、王五共同创立了兰图科技",
            predicate="cofound",
            object="兰图科技",
            confidence=0.9,
            source_memory_id=sid,
        )
        s.add_all([mem, fact])
        s.add_all(
            [
                FactEntity(fact_id=fid, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid, entity_name="李四", role="subject"),
                FactEntity(fact_id=fid, entity_name="王五", role="subject"),
                FactEntity(fact_id=fid, entity_name="兰图科技", role="object"),
            ]
        )
        s.commit()

    with Session(graph_wanbi_db) as s:
        expanded = expand_graph_associations(
            seed_memory_ids=[sid],
            max_hops=2,
            min_edge_conf=0.5,
            session=s,
        )
        # 验证扩散结果中包含了完璧事实的保整信息
        assert len(expanded) >= 1
        wanbi_exp = [e for e in expanded if e.get("relation") == "wanbi:cofound"]
        assert len(wanbi_exp) == 1
        fact_item = wanbi_exp[0]
        assert fact_item["content"] == "张三、李四、王五共同创立了兰图科技"
        assert set(fact_item["entities"]) == {"张三", "李四", "王五", "兰图科技"}
