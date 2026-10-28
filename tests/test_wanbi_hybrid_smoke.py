"""完璧（ADR-0057）与混合检索（hybrid_search）接入不 mock 冒烟测试。"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryItem
from lantai.retrieval import hybrid
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def hybrid_wanbi_env(monkeypatch):
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
    yield session_factory, engine


def test_hybrid_search_wanbi_facts_attachment(hybrid_wanbi_env):
    """不 mock 核心逻辑冒烟：当 query 匹配实体时，检索返回项中挂载完整的 atomic_facts。"""
    session_factory, engine = hybrid_wanbi_env

    # 1. 种子数据：写入一条 MemoryItem 以及对应的完璧 FactAtomic 和 FactEntity 投影
    mid = new_id("mem")
    fid = new_id("fact")
    with session_factory() as s:
        mem = MemoryItem(
            id=mid,
            key="cofound_lantai_tech",
            content="张三、李四和王五在 2025 年共同创办了兰图科技，专注于 Agent 长期记忆系统。",
            lane="general",
            status="active",
            importance=0.9,
            decay_score=1.0,
        )
        fact = FactAtomic(
            id=fid,
            statement="张三、李四、王五于 2025 年共同创办了兰图科技",
            predicate="cofound",
            object="兰图科技",
            confidence=0.95,
            source_memory_id=mid,
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
        sync_fts(s, mid, mem.content)
        s.commit()

    # 2. 调用混合检索（直调真实的 FTS / 关键词降级路径，不 mock 计算逻辑）
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
        results = hybrid.hybrid_search("张三和李四创办了什么公司？", top_k=5, use_rerank=False)

    # 3. 验证返回结果中召回了 MemoryItem 且挂载了保整的完璧原子事实
    assert len(results) >= 1
    r = results[0]
    assert r["memory"]["id"] == mid
    assert "atomic_facts" in r
    facts = r["atomic_facts"]
    assert len(facts) == 1
    f = facts[0]
    assert f["id"] == fid
    assert f["statement"] == "张三、李四、王五于 2025 年共同创办了兰图科技"
    assert f["predicate"] == "cofound"
    assert len(f["entities"]) == 4
    entity_names = {e["name"] for e in f["entities"]}
    assert entity_names == {"张三", "李四", "王五", "兰图科技"}
