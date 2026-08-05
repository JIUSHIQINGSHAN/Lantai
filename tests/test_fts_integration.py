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


def test_hybrid_vector_empty_falls_back_to_fts(engine):
    """向量检索为空（空库/embedding 降级）→ FTS5+BM25 兜底，而非零召回"""
    from remembrance.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))):  # 向量空
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)
    assert results, "向量为空时不应零召回"
    ids = {r["memory"]["id"] for r in results}
    assert fts_id in ids  # FTS 兜底命中


def test_hybrid_vector_empty_trace_marks_fallback(engine):
    """trace=True 时兜底路径记录 fallback_fts 步骤"""
    from remembrance.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))):
        results, trace_steps = hybrid.hybrid_search("咖啡因", top_k=5,
                                                    use_rerank=False, trace=True)
    assert any(s["step"] == "fallback_fts" for s in trace_steps)
    assert fts_id in {r["memory"]["id"] for r in results}


def test_hybrid_explain_breakdown(engine):
    """explain=True → 每条结果附带完整分项（向量路径）"""
    from remembrance.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, vec_hit_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[
                   {"id": vec_hit_id, "distance": 0.3, "metadata": {}}]))):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False,
                                       explain=True)
    assert results
    expl = results[0]["explain"]
    for key in ("vector", "bm25", "fts", "decay", "lane_boost",
                "final", "decay_class", "decay_multiplier"):
        assert key in expl, f"missing explain key: {key}"
    assert expl["decay_class"] == "episodic"
    assert expl["decay_multiplier"] == 1.0  # 刚写入，未老化


def test_hybrid_explain_rerank_keeps_breakdown(engine):
    """reranker 开启时 explain 仍保留原始分项（重排前后可对比）"""
    from remembrance.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, vec_hit_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    fake_rerank = Mock(return_value=[
        {"score": 0.9, "document": "咖啡因摄入记录"}])
    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[
                   {"id": vec_hit_id, "distance": 0.3, "metadata": {}}]))), \
         patch.object(hybrid.settings, "RERANKER_ENABLED", True), \
         patch("remembrance.retrieval.hybrid.rerank", fake_rerank):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=True,
                                       explain=True)
    assert results
    assert "document" in results[0]
    assert results[0]["explain"] is not None
    assert results[0]["explain"]["final"] > 0
    assert results[0]["explain"]["decay_class"] == "episodic"


def test_hybrid_explain_fallback(engine):
    """向量空降级路径 + explain → vector 分项为 0.0，其余齐全"""
    from remembrance.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False,
                                       explain=True)
    assert results
    expl = results[0]["explain"]
    assert expl["vector"] == 0.0
    assert expl["final"] > 0
    assert expl["decay_class"] == "episodic"


def test_hybrid_vector_empty_no_candidates_returns_empty(engine):
    """兜底路径也无 FTS 候选 → 返回空（不炸）"""
    from remembrance.retrieval import hybrid

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))):
        results = hybrid.hybrid_search("完全不存在的记忆关键词xyz", top_k=5,
                                       use_rerank=False)
    assert results == []
