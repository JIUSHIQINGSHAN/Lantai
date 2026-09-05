"""lane 级 ACL（Ticket 08）测试。

allowed_lanes / lane_allowed / filter_results_by_lanes 纯函数不 mock；
路由用 TestClient + monkeypatch settings.AGENT_LANE_BINDINGS（默认关闭回归）。
"""
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import allowed_lanes, filter_results_by_lanes, lane_allowed
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, sync_fts

# ── 纯函数：三态 ───────────────────────────────────────────────

def test_allowed_lanes_states(monkeypatch):
    """未启用 → None；绑定 → 集合；未绑定 → 空集。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    assert allowed_lanes("any") is None

    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    assert allowed_lanes("agent-a") == ["fact"]
    assert allowed_lanes("agent-b") == []


def test_lane_allowed_states(monkeypatch):
    """未启用 → 全放行；启用后越界 → False。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    assert lane_allowed("agent-a", "rule") is True

    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    assert lane_allowed("agent-a", "fact") is True
    assert lane_allowed("agent-a", "rule") is False


def test_filter_results_by_lanes_shapes():
    """兼容两种结果形态；未启用原样返回。"""
    results = [
        {"score": 0.9, "memory": {"id": "m1", "lane": "fact"}},
        {"score": 0.8, "memory": {"id": "m2", "lane": "rule"}},
        {"score": 0.7, "document": "FTS 兜底无 lane"},
    ]
    assert filter_results_by_lanes(results, None) == results
    filtered = filter_results_by_lanes(results, ["fact"])
    assert [r["score"] for r in filtered] == [0.9]
    # 无 lane 的 FTS 兜底视为 general：不在绑定集 → 宁 miss 不放行
    assert filter_results_by_lanes(results, ["general"]) == [results[2]]


# ── 路由：403 与过滤接线 ───────────────────────────────────────

@pytest.fixture()
def client():
    with TestClient(_app()) as c:
        yield c


def _app():
    from api_server import app
    return app


def test_acl_off_default_no_header_ok(client):
    """默认关闭：不带 X-Agent-Id 访问受保护端点正常。"""
    r = client.get("/stats")
    assert r.status_code == 200


def test_acl_on_requires_bound_agent(client, monkeypatch):
    """启用后：缺 header / 未绑定 → 403；绑定 → 200。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    assert client.get("/stats").status_code == 403
    assert client.get("/stats", headers={"X-Agent-Id": "agent-b"}).status_code == 403
    assert client.get("/stats", headers={"X-Agent-Id": "agent-a"}).status_code == 200


def test_acl_write_rejects_out_of_bound_lane(client, monkeypatch):
    """写入越界 lane → 403 不落库。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    r = client.post("/add", headers={"X-Agent-Id": "agent-a"},
                    json={"title": "t", "content": "这是一条超过十个字的内容",
                          "lane": "rule"})
    assert r.status_code == 403


@pytest.fixture()
def search_env():
    """真实 SQLite（含 FTS）+ TestClient；仅 mock embedding/向量存储/_try_log。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.api.routes_search._try_log", return_value=None):
        from api_server import app
        with TestClient(app) as c:
            yield c, session_factory


def test_acl_import_rejects_out_of_bound_lane(search_env, monkeypatch):
    """REST /import/jsonl：ACL 启用时越界 lane 行记 errors 不落库（403 语义）。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    client, session_factory = search_env
    text = (
        '{"content": "绑定lane事实", "lane": "fact"}\n'
        '{"content": "越界规则", "lane": "rule"}\n'
    )
    r = client.post("/import/jsonl", headers={"X-Agent-Id": "agent-a"},
                    json={"text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 1
    assert len(body["errors"]) == 1
    assert "ACL" in body["errors"][0]["reason"]
    with session_factory() as s:
        rows = s.exec(select(MemoryItem)).all()
        assert len(rows) == 1
        assert rows[0].lane == "fact"


def test_acl_search_filters_results(search_env, monkeypatch):
    """真实 SQLite + 真实检索路径（仅 mock 外部依赖）：结果按绑定 lane 收窄。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    client, session_factory = search_env
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
    with patch("lantai.api.routes_search.relevance_check",
               return_value={"needs_memory": True, "reason": "test"}):
        r = client.post("/search", headers={"X-Agent-Id": "agent-a"},
                        json={"query": query, "top_k": 5})
    assert r.status_code == 200
    results = r.json()["results"]
    # 真实检索命中 fact+rule 两条，ACL 收窄后只剩 fact
    assert len(results) == 1
    assert results[0]["memory"]["lane"] == "fact"
