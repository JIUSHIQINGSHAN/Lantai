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
        r = client.post("/retrieval/backfill",
                        json={"event_id": "ev_1", "used_ids": ["mem_1"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["used_count"] == 1
    m.assert_called_once_with("ev_1", ["mem_1"])


def test_backfill_empty_used_ids(client):
    """used_ids 可空（生成侧没用到记忆也算标注）。"""
    with patch("lantai.api.routes_retrieval.backfill_used_ids") as m:
        r = client.post("/retrieval/backfill",
                        json={"event_id": "ev_1", "used_ids": []})
    assert r.status_code == 200
    m.assert_called_once_with("ev_1", [])


def test_backfill_missing_event_id(client):
    r = client.post("/retrieval/backfill",
                    json={"used_ids": ["mem_1"]})
    assert r.status_code == 422  # pydantic 校验失败


def test_backfill_non_list_used_ids(client):
    r = client.post("/retrieval/backfill",
                    json={"event_id": "ev_1", "used_ids": "mem_1"})
    assert r.status_code == 422

