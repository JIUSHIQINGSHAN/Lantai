"""/usage 端点测试：最近 7 天 GROUP BY 聚合 + 缺日补零"""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import remembrance.storage.db as db_module
from remembrance.api.routes_health import usage
from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.models.tables import MemoryItem


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:",
                      connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


def _add(engine, days_ago: float = 0.0) -> str:
    from datetime import timedelta
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(MemoryItem(
            id=mid, memory_type="general", key=mid, content="内容内容内容内容内容",
            lane="general", status="active", decay_score=1.0,
            created_at=utcnow() - timedelta(days=days_ago)))
        s.commit()
    return mid


def test_usage_returns_seven_days_with_zero_fill(engine):
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage()
    daily = res["daily_new"]
    assert len(daily) == 7  # 恰好 7 天，缺日补 0
    assert all(v >= 0 for v in daily.values())


def test_usage_counts_today(engine):
    from datetime import date
    _add(engine)  # 今天
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage()
    today = str(date.today())
    assert today in res["daily_new"]
    assert res["daily_new"][today] >= 1


def test_usage_ignores_old_records(engine):
    """8 天前的记忆不计入最近 7 天窗口"""
    _add(engine, days_ago=8.0)
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage()
    assert sum(res["daily_new"].values()) == 0
