"""三态去重（dedup）测试：merge / update / insert。"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from sqlmodel.pool import StaticPool

from lantai.storage import db
from lantai.models.tables import MemoryItem
from lantai.core.ids import new_id


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    db.engine = engine
    SQLModel.metadata.create_all(engine)

    def mock_search(content_or_vec, top_k=8, where=None):
        return getattr(mock_search, "results", [])

    monkeypatch.setattr("lantai.services.memory_service.vector_store.search", mock_search)
    monkeypatch.setattr("lantai.parsing.extractor.chat_json",
                        lambda *a, **kw: {"summary": "t", "claims": [], "methods": [],
                                          "constraints": [], "actions": [], "topic": [],
                                          "extractor_confidence": 0.5})

    from api_server import app
    with TestClient(app) as c:
        c.mock_search = mock_search
        yield c


def _seed_memory() -> str:
    with Session(db.engine) as s:
        mem = MemoryItem(
            id=new_id("mem"),
            memory_type="preference", key="pref_coffee",
            content="用户喜欢 coffee", lane="preference",
            status="active", importance=0.5)
        s.add(mem)
        s.commit()
        s.refresh(mem)
        return mem.id


def test_dedup_merge(client):
    mem_id = _seed_memory()
    client.mock_search.results = [{"id": mem_id, "document": "用户喜欢 coffee",
                                   "metadata": {}, "distance": 0.1}]  # sim=0.9 ≥ 0.80
    resp = client.post("/add", json={"title": "x", "content": "用户喜欢喝咖啡测试数据"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("dedup_action") == "merge"
    assert data.get("target_memory_id") == mem_id


def test_dedup_update(client):
    mem_id = _seed_memory()
    client.mock_search.results = [{"id": mem_id, "document": "用户喜欢 coffee",
                                   "metadata": {}, "distance": 0.3}]  # sim=0.7 ∈ [0.65, 0.80)
    resp = client.post("/add", json={"title": "x", "content": "用户最近爱喝拿铁测试数据"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("dedup_action") == "update"
    assert "proposal_id" in data


def test_dedup_insert(client):
    client.mock_search.results = []  # 无相似 → insert
    resp = client.post("/add", json={"title": "x", "content": "全新的记忆内容条目补充"})
    assert resp.status_code == 200
    data = resp.json()
    assert "dedup_action" not in data
    assert "candidate_id" in data
