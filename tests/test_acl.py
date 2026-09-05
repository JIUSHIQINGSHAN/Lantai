import pytest
from fastapi.testclient import TestClient
from sqlmodel import select
import lantai.storage.db as db_module
from lantai.core.auth import create_api_key
from lantai.models.tables import MemoryItem
import lantai.models.tables
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def client():
    from unittest.mock import patch
    import lantai.storage.db as db_module
    from api_server import app
    from sqlmodel import create_engine, Session, SQLModel
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    def get_test_session():
        return Session(test_engine)
        
    with patch.object(db_module, "get_session", get_test_session):
        init_fts(test_engine.raw_connection())
        if True:
            with TestClient(app) as c:
                yield c

@pytest.fixture(autouse=True)
def setup_keys(client):
    with db_module.get_session() as s:
        raw_a, key_a = create_api_key("agent-a", ["fact"])
        raw_b, key_b = create_api_key("agent-b", ["rule"])
        s.add(key_a)
        s.add(key_b)
        s.commit()
        
    return {"a": raw_a, "b": raw_b}

def test_acl_search_filters_results(client, setup_keys):
    import lantai.storage.db as db_module
    session_factory = db_module.get_session
    query = "服务部署配置上线迁移回滚演练"
    with session_factory() as s:
        for lane, suffix in (("fact", "备忘甲"), ("rule", "备忘乙")):
            m = MemoryItem(
                id=f"m-{lane}", key=f"k-{lane}", memory_type="memory",
                content=query + suffix, lane=lane, status="active",
            )
            s.add(m)
            s.flush()
            sync_fts(s, m.id, m.content)
        s.commit()
    
    from unittest.mock import patch
    with patch("lantai.api.routes_search.relevance_check", return_value={"needs_memory": True, "reason": "test"}):
        r = client.post("/search", headers={"Authorization": f"Bearer {setup_keys['a']}"}, json={"query": query, "top_k": 5})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["memory"]["lane"] == "fact"

def test_acl_write_rejects_out_of_bound_lane(client, setup_keys):
    # agent-a can only write to "fact"
    resp = client.post(
        "/add",
        headers={"Authorization": f"Bearer {setup_keys['a']}"},
        json={"title": "Test", "content": "This is a long enough content to pass validation for rule", "lane": "rule"}
    )
    print(resp.json())
    assert resp.status_code == 403
    
    resp_ok = client.post(
        "/add",
        headers={"Authorization": f"Bearer {setup_keys['a']}"},
        json={"title": "Test", "content": "This is a long enough content to pass validation for fact", "lane": "fact"}
    )
    assert resp_ok.status_code == 200

def test_acl_import_rejects_out_of_bound_lane(client, setup_keys):
    lines = '{"content": "这是一个很长的事实用于测试", "lane": "fact"}\n{"content": "这是一个很长的规则用于测试", "lane": "rule"}\n'
    resp = client.post(
        "/import/jsonl",
        headers={"Authorization": f"Bearer {setup_keys['a']}"},
        json={"text": lines}
    )
    assert resp.status_code == 200
    print(resp.json())
    assert resp.json()["imported"] == 1
    assert len(resp.json()["errors"]) == 1
