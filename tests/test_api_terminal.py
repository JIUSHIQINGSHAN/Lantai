import pytest
from fastapi.testclient import TestClient

from api_server import app
from lantai.storage.db import get_session

client = TestClient(app)
# 需要传入 mock 的 agent_id
headers = {"X-API-Key": "test-key-123", "X-Agent-ID": "test-agent"}

@pytest.fixture(autouse=True)
def setup_db():
    session = get_session()
    conn = session.connection().connection
    conn.execute("DELETE FROM memoryitem")
    conn.execute("DELETE FROM edge")
    conn.execute('''INSERT INTO memoryitem (id, content, domain, lane, importance, confidence) 
                  VALUES ('test1', 'A core memory', 'user', 'general', 0.9, 0.9)''')
    conn.execute('''INSERT INTO memoryitem (id, content, domain, lane, importance, confidence) 
                  VALUES ('test2', 'A fragmented memory', 'user', 'general', 0.5, 0.5)''')
    conn.commit()
    yield
    conn.execute("DELETE FROM memoryitem")
    conn.execute("DELETE FROM edge")
    conn.commit()
    session.close()

def test_terminal_chat_stream():
    # 测试 SSE 端点能否正常返回事件流
    response = client.post("/terminal/chat", json={"query": "test query"}, headers=headers)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    content = response.content.decode("utf-8")
    assert "event: step" in content
    assert "event: gate" in content
    assert "event: complete" in content

def test_terminal_graph():
    response = client.get("/terminal/graph", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) >= 2
    ids = [n["id"] for n in data["nodes"]]
    assert "test1" in ids
    assert "test2" in ids

def test_terminal_memory_crud():
    # READ
    resp1 = client.get("/terminal/memory/test1", headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["content"] == "A core memory"

    # UPDATE
    resp2 = client.put("/terminal/memory/test1", json={"importance": 0.95}, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True
    
    resp_check = client.get("/terminal/memory/test1", headers=headers)
    assert resp_check.json()["importance"] == 0.95

    # DELETE
    resp3 = client.delete("/terminal/memory/test1", headers=headers)
    assert resp3.status_code == 200
    
    resp_check_deleted = client.get("/terminal/memory/test1", headers=headers)
    assert resp_check_deleted.status_code == 404

def test_terminal_merge():
    # 合并 test2 到 test1
    response = client.post("/terminal/merge", json={"source_id": "test2", "target_id": "test1"}, headers=headers)
    assert response.status_code == 200
    
    # 源节点应被删除
    assert client.get("/terminal/memory/test2", headers=headers).status_code == 404
    
    # 目标节点内容合并
    merged = client.get("/terminal/memory/test1", headers=headers).json()
    assert "A core memory" in merged["content"]
    assert "A fragmented memory" in merged["content"]
