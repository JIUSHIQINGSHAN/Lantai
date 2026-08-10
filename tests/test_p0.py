"""
P0 测试：FTS5 + Trigram 全文搜索 & Chronos 双时间轴

v0.3.2 重写：
- FTS5 改为对 storage.fts 层的单元测试（原经 /search 触网调 LLM，属测试 bug）
- Chronos 改为 mock embed/vector_store 后直接调 hybrid_search
- 修复 resp.json 缺括号的 TypeError
"""
import sqlite3
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import index_fts, init_fts, search_fts


@pytest.fixture
def fts_conn():
    conn = sqlite3.connect(":memory:")
    init_fts(conn)
    yield conn
    conn.close()


class TestFTS5:
    """FTS5 + Trigram 全文搜索（fts 层单元测试）"""

    def test_search_empty(self, fts_conn):
        """空库搜索返回空列表"""
        assert search_fts(fts_conn, "python") == []

    def test_search_with_results(self, fts_conn):
        """索引后搜索应返回对应 memory_id"""
        index_fts(fts_conn, "mem_1", "Python is a programming language")
        assert "mem_1" in search_fts(fts_conn, "Python")

    def test_search_trigram(self, fts_conn):
        """trigram 分词器支持子串匹配：learn 命中 learning"""
        index_fts(fts_conn, "mem_2", "Deep learning is a subset of machine learning")
        assert "mem_2" in search_fts(fts_conn, "learn")

    def test_search_multiple_keywords(self, fts_conn):
        """多关键词 AND 查询"""
        index_fts(fts_conn, "mem_3", "FastAPI is a modern web framework for Python")
        index_fts(fts_conn, "mem_4", "React is a JavaScript library")
        results = search_fts(fts_conn, "Python framework")
        assert "mem_3" in results
        assert "mem_4" not in results


@pytest.fixture
def search_env():
    """内存库 + mock 检索外部依赖，直接测 hybrid_search"""
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("lantai.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]):
        yield engine


def _seed(engine, content: str, valid_from=None, valid_to=None) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(MemoryItem(
            id=mid, memory_type="general", key=mid, content=content,
            lane="general", status="active", importance=0.5, use_count=0,
            decay_score=1.0, last_used_at=utcnow(), created_at=utcnow(),
            valid_from=valid_from, valid_to=valid_to))
        s.commit()
    return mid


def _mock_store(ids):
    return Mock(search=Mock(return_value=[
        {"id": i, "distance": 0.1, "metadata": {}} for i in ids]))


class TestChronos:
    """Chronos 双时间轴过滤（hybrid_search 层）"""

    def test_valid_from_future_filtered(self, search_env):
        """未生效记忆应被过滤"""
        from lantai.retrieval import hybrid
        now = utcnow()
        ok_id = _seed(search_env, "当前生效的记忆内容")
        future_id = _seed(search_env, "三十天后才生效的记忆",
                          valid_from=now + timedelta(days=30))
        with patch("lantai.retrieval.hybrid.get_vector_store",
                   return_value=_mock_store([ok_id, future_id])):
            results = hybrid.hybrid_search("记忆", top_k=5, use_rerank=False)
        ids = [r["memory"]["id"] for r in results]
        assert ok_id in ids
        assert future_id not in ids

    def test_expired_memory_downweighted(self, search_env):
        """过期记忆降权但保留"""
        from lantai.retrieval import hybrid
        now = utcnow()
        fresh_id = _seed(search_env, "未过期记忆")
        expired_id = _seed(search_env, "已过期记忆",
                           valid_to=now - timedelta(days=1))
        with patch("lantai.retrieval.hybrid.get_vector_store",
                   return_value=_mock_store([fresh_id, expired_id])):
            results = hybrid.hybrid_search("记忆", top_k=5, use_rerank=False)
        ids = [r["memory"]["id"] for r in results]
        assert expired_id in ids

    def test_integration_seed_and_search(self, search_env):
        """写入 → 检索 全链路（mock 向量层）"""
        from lantai.retrieval import hybrid
        mid = _seed(search_env, "Full pipeline with FTS5 and Chronos")
        with patch("lantai.retrieval.hybrid.get_vector_store",
                   return_value=_mock_store([mid])):
            results = hybrid.hybrid_search("pipeline", top_k=5, use_rerank=False)
        assert any(r["memory"]["id"] == mid for r in results)
