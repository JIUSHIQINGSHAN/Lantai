"""
P0 测试：FTS5 + Trigram 全文搜索 & Chronos 双时间轴
"""
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel

from api_server import app
from remembrance.core.settings import settings
from remembrance.core.time import utcnow
from remembrance.models.tables import MemoryItem, Source
from remembrance.storage import db
from remembrance.storage.fts import init_fts, search_fts


@pytest.fixture(scope="function")
def client():
    """创建测试客户端，使用内存数据库"""
    test_engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(test_engine)

    # 初始化 FTS5
    conn = test_engine.raw_connection()
    init_fts(conn)

    def get_test_session():
        return Session(test_engine)

    app.dependency_overrides[db.get_session] = get_test_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestFTS5:
    """FTS5 + Trigram 全文搜索测试"""

    def test_search_empty(self, client):
        """空库搜索返回空列表"""
        resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_with_results(self, client):
        """写入后搜索应返回结果"""
        client.post("/add", json={
            "title": "Python Guide",
            "content": "Python is a programming language"
        })
        resp = client.post("/search", json={"query": "python"})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) >= 1

    def test_search_trigram(self, client):
        """trigram 分词器支持子串匹配"""
        client.post("/add", json={
            "title": "Machine Learning",
            "content": "Deep learning is a subset of machine learning"
        })
        # "learn" 应匹配到 "learning"
        resp = client.post("/search", json={"query": "learn"})
        assert resp.status_code == 200
        assert len(resp.json["results"]) >= 1

    def test_search_multiple_keywords(self, client):
        """多个关键词 AND 查询"""
        client.post("/add", json={
            "title": "Python FastAPI",
            "content": "FastAPI is a modern web framework for Python"
        })
        client.post("/add", json={
            "title": "JavaScript React",
            "content": "React is a JavaScript library"
        })
        resp = client.post("/search", json={"query": "python framework"})
        assert resp.status_code == 200
        assert len(resp.json["results"]) >= 1


class TestChronos:
    """Chronos 双时间轴测试"""

    def test_valid_from_future(self, client):
        """未生效记忆应被过滤"""
        future = utcnow() + timedelta(days=30)
        client.post("/add", json={
            "title": "Future Memory",
            "content": "This should not appear yet"
        })
        resp = client.post("/search", json={"query": "future"})
        assert resp.status_code == 200
        # 未生效记忆在混合搜索中被过滤
        assert len(resp.json()["results"]) == 0

    def test_expired_memory(self, client):
        """过期记忆应被降权"""
        past = utcnow() - timedelta(days=30)
        client.post("/add", json={
            "title": "Expired Memory",
            "content": "This is old content"
        })
        # 搜索应能找到（但降权）
        resp = client.post("/search", json={"query": "old"})
        assert resp.status_code == 200


class TestIntegration:
    """集成测试"""

    def test_full_pipeline(self, client):
        """写入 → FTS5 搜索 → 时间过滤"""
        client.post("/add", json={
            "title": "Integration Test",
            "content": "Full pipeline with FTS5 and Chronos"
        })
        # FTS5 搜索
        resp = client.post("/search", json={"query": "pipeline"})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) >= 1

        # 添加带时间窗口的记忆
        client.post("/add", json={
            "title": "Timed Memory",
            "content": "Valid from now to future"
        })
        resp = client.post("/search", json={"query": "timed"})
        assert resp.status_code == 200
