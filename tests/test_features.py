"""
T04-T11: 功能测试——coalesce / fastpath / search_trace / health / dedup
"""
import json
import warnings
from unittest.mock import patch, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import remembrance.storage.db as db_module
from remembrance.core.settings import settings


@pytest.fixture(scope="function")
def client():
    test_engine = create_engine(
        "sqlite:///:memory:", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("remembrance.retrieval.reranker.rerank", return_value=[]), \
         patch("remembrance.storage.vector_store.ChromaVectorStore"), \
         patch("remembrance.retrieval.hybrid.embed", return_value=[[0.1]*8]), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))), \
         patch("remembrance.parsing.extractor.chat_json",
               return_value={"summary": "test", "claims": [], "methods": [],
                             "constraints": [], "actions": [], "topic": [],
                             "extractor_confidence": 0.5}):
        from api_server import app
        with TestClient(app) as c:
            yield c


class TestFastpath:
    """T05: fastpath 白名单直写"""

    def test_self_declaration(self):
        from remembrance.parsing.fastpath import fastpath_check
        result = fastpath_check("我叫张三")
        assert result is not None
        assert result["lane"] == "fact"
        assert "张三" in result["summary"]

    def test_preference(self):
        from remembrance.parsing.fastpath import fastpath_check
        result = fastpath_check("我喜欢Python")
        assert result is not None
        assert result["lane"] == "preference"

    def test_explicit_instruction(self):
        from remembrance.parsing.fastpath import fastpath_check
        result = fastpath_check("记住：明天开会")
        assert result is not None
        assert result["lane"] == "general"

    def test_no_match(self):
        from remembrance.parsing.fastpath import fastpath_check
        result = fastpath_check("今天天气怎么样")
        assert result is None

    def test_too_short(self):
        from remembrance.parsing.fastpath import fastpath_check
        result = fastpath_check("好")
        assert result is None


class TestCoalesceBuffer:
    """T04: coalesce 缓冲"""

    def test_buffer_add(self):
        from remembrance.ingestion.coalesce import CoalesceBuffer
        buf = CoalesceBuffer()
        result = buf.add("user1", "general", "hello world")
        assert result.get("buffered") is True

    def test_buffer_flush_on_max_parts(self):
        from remembrance.ingestion.coalesce import CoalesceBuffer
        buf = CoalesceBuffer()
        # max_parts=8 by default
        for i in range(8):
            result = buf.add("user1", "general", f"message {i}")
        # 第 8 条应该触发 flush
        assert result.get("flushed") is True
        assert result.get("count") == 8

    def test_water_level(self):
        from remembrance.ingestion.coalesce import CoalesceBuffer
        buf = CoalesceBuffer()
        buf.add("user1", "general", "hello")
        level = buf.water_level()
        assert level["active_keys"] == 1
        assert level["total_messages"] == 1


class TestHealthStats:
    """T07: health + stats 端点"""

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_health_deep(self, client):
        resp = client.get("/health/deep")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_memories" in data
        assert "by_lane" in data
        assert "coalesce_buffer" in data


class TestSearchTrace:
    """T06: search_trace"""

    def test_search_no_trace(self, client):
        resp = client.post("/search", json={"query": "test query here"})
        assert resp.status_code == 200
        assert "trace" not in resp.json()

    def test_search_with_trace(self, client):
        resp = client.post("/search?trace=true", json={"query": "记得向量检索的方案"})
        assert resp.status_code == 200
        data = resp.json()
        assert "trace" in data
        steps = {s["step"] for s in data["trace"]}
        assert "intent" in steps


class TestForgettingArchived:
    """T16: forgetting archived 测试"""

    def test_decay_below_threshold_auto_archived(self, client):
        from datetime import timedelta
        from remembrance.memory.forgetting import apply_forgetting
        from remembrance.models.tables import MemoryItem
        from remembrance.core.time import utcnow
        from remembrance.core.ids import new_id
        from sqlmodel import select
        from remembrance.storage import db as db_mod

        with db_mod.get_session() as s:
            old = MemoryItem(
                id=new_id("mem"),
                memory_type="general", key="old_working",
                content="临时事项已完成", lane="working",
                status="active", importance=0.1, use_count=0,
                decay_score=0.2,
                last_used_at=utcnow() - timedelta(days=100),
                created_at=utcnow() - timedelta(days=100))
            s.add(old)
            s.commit()
            s.refresh(old)
            mid = old.id

        apply_forgetting()

        with db_mod.get_session() as s:
            mem = s.get(MemoryItem, mid)
            assert mem.status == "archived"

    def test_search_excludes_archived(self, client):
        import inspect
        from remembrance.retrieval import hybrid
        src = inspect.getsource(hybrid)
        assert '.where(MemoryItem.status == "active")' in src
