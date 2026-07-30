"""
Remembrance-System 端到端测试
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from unittest.mock import patch

from api_server import app
import remembrance.storage.db as db_module
from remembrance.storage.db import get_session, init_db
from remembrance.core.settings import settings
from remembrance.models.tables import (
    MemoryItem, MemoryCandidate, RawDocument, Source,
    MemoryProposal, CoreMemoryBlock, MemoryCheckpoint
)


@pytest.fixture(scope="function")
def client():
    """创建测试客户端，使用内存数据库（通过 monkeypatch 替换 engine）"""
    test_engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    # 必须 monkeypatch 模块级 get_session，因为 gate/decision.py 直接调用而非通过 Depends 注入
    with patch.object(db_module, "get_session", get_test_session):
        with TestClient(app) as c:
            yield c


class TestHealth:
    """健康检查测试"""

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_memory_health(self, client):
        resp = client.get("/api/memory/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestAddMemory:
    """写入记忆测试"""

    def test_add_basic(self, client):
        resp = client.post("/add", json={
            "title": "Test",
            "content": "This is a test memory"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "document_id" in data
        assert "candidate_id" in data
        assert data["document_id"].startswith("doc_")
        assert data["candidate_id"].startswith("cand_")

    def test_add_with_tags(self, client):
        resp = client.post("/add", json={
            "title": "Tagged",
            "content": "Memory with tags",
            "tags": ["test", "python"]
        })
        assert resp.status_code == 200

    def test_add_duplicate_content(self, client):
        """重复内容应返回已有文档"""
        content = "Duplicate test content"
        client.post("/add", json={"title": "First", "content": content})
        resp = client.post("/add", json={"title": "Second", "content": content})
        assert resp.status_code == 200


class TestSearch:
    """搜索测试"""

    def test_search_empty(self, client):
        resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_with_results(self, client):
        client.post("/add", json={"title": "Python", "content": "Python is great"})
        resp = client.post("/search", json={"query": "python", "top_k": 5})
        assert resp.status_code == 200


class TestCoreMemory:
    """CoreMemory 测试"""

    def test_get_empty(self, client):
        resp = client.get("/core-memory")
        assert resp.status_code == 200
        assert isinstance(resp.json()["blocks"], list)

    def test_put_core_memory(self, client):
        resp = client.put("/core-memory?block=identity&content=test%20user")
        assert resp.status_code == 200
        assert resp.json()["block"] == "identity"

    def test_put_invalid_block(self, client):
        resp = client.put("/core-memory?block=invalid&content=test")
        assert resp.status_code == 400


class TestSources:
    """来源管理测试"""

    def test_add_source(self, client):
        resp = client.post("/sources", json={
            "kind": "rss",
            "config": {"url": "https://example.com/feed"},
            "enabled": True
        })
        assert resp.status_code == 200
        assert resp.json()["kind"] == "rss"

    def test_list_sources(self, client):
        client.post("/sources", json={
            "kind": "rss",
            "config": {"url": "https://example.com/feed"}
        })
        resp = client.get("/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) >= 1


class TestGate:
    """闸门测试"""

    def test_add_reject_short_content(self, client):
        """min_length=10 约束：过短内容应返回 422"""
        resp = client.post("/add", json={
            "title": "short",
            "content": "y"
        })
        assert resp.status_code == 422

    def test_gate_reject_low_confidence(self, client):
        """低置信度候选应被 Gate 拒绝"""
        resp = client.post("/add", json={
            "title": "minimal content",
            "content": "1234567890"  # 10 字符，刚好通过校验
        })
        assert resp.status_code == 200
        cand_id = resp.json()["candidate_id"]
        resp = client.post("/gate", json={"candidate_id": cand_id})
        assert resp.status_code == 200
        # 极低内容 → 提取器兜底 0.3 < GATE_MIN 0.55 → REJECT
        assert resp.json()["decision"] == "reject"


class TestEvolution:
    """演化测试"""

    def test_list_proposals(self, client):
        resp = client.get("/proposals")
        assert resp.status_code == 200

    def test_evolve_run(self, client):
        resp = client.post("/evolve/run")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestFeedback:
    """反馈测试"""

    def test_feedback(self, client):
        resp = client.post("/add", json={
            "title": "Test",
            "content": "Feedback test"
        })
        mem_id = resp.json()["candidate_id"]
        resp = client.post("/feedback", json={
            "memory_id": mem_id,
            "query": "test",
            "helped": True,
            "user_accepted": True
        })
        assert resp.status_code == 200


class TestIntegration:
    """集成测试：完整流程"""

    def test_full_pipeline(self, client):
        """写入 → 搜索 → 反馈完整流程"""
        # 1. 写入
        resp = client.post("/add", json={
            "title": "Integration Test",
            "content": "Full pipeline test with Python and FastAPI"
        })
        assert resp.status_code == 200

        # 2. 搜索
        resp = client.post("/search", json={"query": "python", "top_k": 5})
        assert resp.status_code == 200

        # 3. CoreMemory
        resp = client.put("/core-memory?block=task&content=testing")
        assert resp.status_code == 200

        resp = client.get("/core-memory")
        assert resp.status_code == 200
