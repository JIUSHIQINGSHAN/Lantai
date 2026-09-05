"""Step 8 人工验证路由测试——真实内存 SQLite + 真实业务逻辑。

覆盖：POST /verification（pass/fail/降权链路）、GET /verification/stats、
输入校验 422。薄路由层不 mock（record_verification_result 真实执行，
与 test_param_reliability 共用同一套不 mock 纪律）。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401
import lantai.parameters.trust_models  # noqa: F401
import lantai.storage.db as db_module
from lantai.api.routes_verification import router


@pytest.fixture(scope="function")
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        db_module, "get_session", session_factory
    ):
        yield TestClient(app)


def test_record_pass_ok(client):
    r = client.post("/verification",
                    json={"venue_class": "journal", "passed": True})
    assert r.status_code == 200
    body = r.json()
    assert body["venue_class"] == "journal"
    assert body["pass_count"] == 1
    assert body["fail_count"] == 0
    assert body["fail_streak"] == 0
    assert body["penalty"] == 1.0


def test_record_fail_streak_penalizes(client):
    """连续 3 次失败（≥ PENALTY_FAIL_STREAK=2）→ 降权系数 < 1.0。"""
    for _ in range(3):
        r = client.post("/verification",
                        json={"venue_class": "preprint", "passed": False})
        assert r.status_code == 200
    body = r.json()
    assert body["fail_streak"] == 3
    assert body["fail_count"] == 3
    assert body["penalty"] < 1.0


def test_record_note_accepted(client):
    r = client.post("/verification",
                    json={"venue_class": "workshop", "passed": False,
                          "note": "重复实验后结论不可靠"})
    assert r.status_code == 200


def test_missing_venue_class_422(client):
    r = client.post("/verification", json={"passed": True})
    assert r.status_code == 422


def test_missing_passed_422(client):
    r = client.post("/verification", json={"venue_class": "journal"})
    assert r.status_code == 422


def test_empty_venue_class_422(client):
    r = client.post("/verification",
                    json={"venue_class": "", "passed": True})
    assert r.status_code == 422


def test_stats_lists_all(client):
    client.post("/verification", json={"venue_class": "journal", "passed": True})
    for _ in range(3):
        client.post("/verification", json={"venue_class": "preprint", "passed": False})
    r = client.get("/verification/stats")
    assert r.status_code == 200
    rows = {row["venue_class"]: row for row in r.json()["stats"]}
    assert rows["journal"]["pass_count"] == 1
    assert rows["journal"]["penalty"] == 1.0
    assert rows["preprint"]["fail_streak"] == 3
    assert rows["preprint"]["penalty"] < 1.0

