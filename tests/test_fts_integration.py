"""FTS5 集成测试：同事务同步 + 检索融合 + 追加召回 + BM25 缓存"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import remembrance.storage.db as db_module
from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.models.tables import MemoryItem
from remembrance.storage.fts import init_fts, search_fts, sync_fts


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:",
                      connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    return e


def _add_mem(engine, content: str) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(MemoryItem(
            id=mid, memory_type="general", key=mid, content=content,
            lane="general", status="active", importance=0.5, use_count=0,
            decay_score=1.0, last_used_at=utcnow(), created_at=utcnow()))
        s.commit()
    return mid


def test_sync_fts_same_transaction(engine):
    """sync_fts 与记忆写入同事务：commit 后 FTS 可见"""
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(MemoryItem(
            id=mid, memory_type="general", key=mid, content="用户喜欢喝咖啡",
            lane="general", status="active", importance=0.5, use_count=0,
            decay_score=1.0, last_used_at=utcnow(), created_at=utcnow()))
        sync_fts(s, mid, "用户喜欢喝咖啡")
        s.commit()
    with engine.connect() as conn:
        assert mid in search_fts(conn.connection.driver_connection, "喝咖啡")


def test_sync_fts_update(engine):
    """同 id 再同步 = 覆盖（先删后插）"""
    mid = _add_mem(engine, "旧内容")
    with Session(engine) as s:
        sync_fts(s, mid, "新内容")
        s.commit()
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        assert mid in search_fts(c, "新内容")
        assert mid not in search_fts(c, "旧内容")


def test_sync_fts_delete(engine):
    """content=None 删除索引"""
    mid = _add_mem(engine, "要删除的记忆")
    with Session(engine) as s:
        sync_fts(s, mid, None)
        s.commit()
    with engine.connect() as conn:
        assert mid not in search_fts(conn.connection.driver_connection, "要删除的记忆")


def test_hybrid_fts_extra_recall(engine):
    """FTS 命中但向量未命中的记忆被追加召回"""
    from remembrance.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "向量命中的记忆")
    fts_only_id = _add_mem(engine, "咖啡因摄入记录")
    # 向量只返回 vec_hit；fts 用真实表
    with Session(engine) as s:
        sync_fts(s, fts_only_id, "咖啡因摄入记录")
        sync_fts(s, vec_hit_id, "向量命中的记忆")
        s.commit()

    def get_test_session():
        return Session(engine)

    def fake_store(ids):
        return Mock(search=Mock(return_value=[
            {"id": i, "distance": 0.3, "metadata": {}} for i in ids]))

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=fake_store([vec_hit_id])):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)
    ids = {r["memory"]["id"] for r in results}
    assert vec_hit_id in ids
    assert fts_only_id in ids  # 追加召回生效
