"""REST 回填路由冒烟测试——mock backfill_used_ids，验证路由与输入校验。"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lantai.api.routes_retrieval import router


@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_backfill_ok(client):
    with patch("lantai.api.routes_retrieval.backfill_used_ids") as m:
        r = client.post("/retrieval/backfill", json={"event_id": "ev_1", "used_ids": ["mem_1"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["used_count"] == 1
    # 归属（票 01c）：路由现在把 ctx 传下去。DEV MODE 无凭证 → 回落
    # Principal(user_id="default")，**不是 None**（同票 05 口径：
    # "无凭证的本机请求"与"内部 worker"是两回事）
    assert m.call_count == 1
    args, kwargs = m.call_args
    assert args == ("ev_1", ["mem_1"])
    assert kwargs["principal"] is not None
    assert kwargs["principal"].user_id == "default"


def test_backfill_empty_used_ids(client):
    """used_ids 可空（生成侧没用到记忆也算标注）。"""
    with patch("lantai.api.routes_retrieval.backfill_used_ids") as m:
        r = client.post("/retrieval/backfill", json={"event_id": "ev_1", "used_ids": []})
    assert r.status_code == 200
    assert m.call_count == 1
    args, kwargs = m.call_args
    assert args == ("ev_1", [])
    assert kwargs["principal"] is not None


def test_backfill_missing_event_id(client):
    r = client.post("/retrieval/backfill", json={"used_ids": ["mem_1"]})
    assert r.status_code == 422  # pydantic 校验失败


def test_backfill_non_list_used_ids(client):
    r = client.post("/retrieval/backfill", json={"event_id": "ev_1", "used_ids": "mem_1"})
    assert r.status_code == 422
