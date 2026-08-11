"""lane 级 ACL（Ticket 08）测试。

allowed_lanes / lane_allowed / filter_results_by_lanes 纯函数不 mock；
路由用 TestClient + monkeypatch settings.AGENT_LANE_BINDINGS（默认关闭回归）。
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lantai.core.settings import settings
from lantai.core.acl import allowed_lanes, filter_results_by_lanes, lane_allowed


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


def test_acl_search_filters_results(client, monkeypatch):
    """检索结果按绑定 lane 收窄（hybrid_search 为外部依赖，mock 接线）。"""
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    mixed = [
        {"score": 0.9, "memory": {"id": "m1", "lane": "fact", "content": "事实"}},
        {"score": 0.8, "memory": {"id": "m2", "lane": "rule", "content": "规则"}},
    ]
    with patch("lantai.api.routes_search.hybrid_search", return_value=mixed), \
         patch("lantai.api.routes_search._try_log", return_value=None), \
         patch("lantai.api.routes_search.relevance_check",
               return_value={"needs_memory": True, "reason": "test"}):
        r = client.post("/search", headers={"X-Agent-Id": "agent-a"},
                        json={"query": "服务部署配置上线迁移回滚演练", "top_k": 5})
        assert r.status_code == 200
        results = r.json()["results"]
        assert [x["memory"]["lane"] for x in results] == ["fact"]
