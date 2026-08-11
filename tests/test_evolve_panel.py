"""EVOLVE 检索质量看板（Ticket 05）核心函数 + 路由冒烟测试。

recent_retrieval_events 真实 DB 直调（不 mock）；页面/端点只验证可达与契约。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.models.tables import RetrievalEvent


@pytest.fixture()
def ev_env():
    """内存 SQLite 真实建表 + patch 仅 db.get_session。"""
    import lantai.models.tables  # noqa
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _event(i, created_at, zero=False, noise=False, lane="general"):
    return RetrievalEvent(
        id=f"rev_{i}", trace_id=f"t{i}", query_text=f"查询 {i}",
        query_norm_hash=f"h{i}", lane=lane, intent_bucket="fact_lookup",
        param_snapshot_hash="p", latency_ms=10 + i,
        zero_result=zero, is_system_noise=noise, created_at=created_at,
    )


def test_recent_retrieval_events_orders_desc(ev_env):
    """真实 DB：新→旧排序 + 字段齐全。"""
    session_factory, _ = ev_env
    now = datetime.now(timezone.utc)
    with session_factory() as s:
        for i, dt in [(1, now - timedelta(hours=2)),
                      (2, now), (3, now - timedelta(hours=1))]:
            s.add(_event(i, dt))
        s.commit()
    from lantai.observability.recall_report import recent_retrieval_events
    events = recent_retrieval_events(limit=10)
    assert [e["id"] for e in events] == ["rev_2", "rev_3", "rev_1"]
    assert events[0]["query"] == "查询 2"
    assert events[0]["zero_result"] is False


def test_recent_retrieval_events_limit_validation(ev_env):
    from lantai.observability.recall_report import recent_retrieval_events
    with pytest.raises(ValueError):
        recent_retrieval_events(limit=0)
    with pytest.raises(ValueError):
        recent_retrieval_events(limit=101)


def test_ui_evolve_served():
    """页面可达：/ui/evolve 200 + 面板标记 + 数据端点引用。"""
    from fastapi.testclient import TestClient
    from api_server import app
    with TestClient(app) as c:
        r = c.get("/ui/evolve")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "检索质量看板" in r.text
        assert "/retrieval/recall-report" in r.text
        assert "/retrieval/recent-events" in r.text


def test_recent_events_endpoint(ev_env):
    """数据端点：/retrieval/recent-events 返回事件流（新→旧）。"""
    session_factory, _ = ev_env
    now = datetime.now(timezone.utc)
    with session_factory() as s:
        s.add(_event(1, now, zero=True))
        s.add(_event(2, now - timedelta(minutes=5)))
        s.commit()
    from fastapi.testclient import TestClient
    from api_server import app
    with TestClient(app) as c:
        r = c.get("/retrieval/recent-events?limit=5")
        assert r.status_code == 200
        events = r.json()["events"]
        assert events[0]["id"] == "rev_1" and events[0]["zero_result"] is True
        assert events[1]["id"] == "rev_2"
