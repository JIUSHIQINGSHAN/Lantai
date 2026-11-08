"""/usage 端点测试：最近 7 天每日新增聚合（票 32 起按本地日归桶）+ 缺日补零"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.routes_health import usage
from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


def _add(engine, days_ago: float = 0.0) -> str:
    from datetime import timedelta

    mid = new_id("mem")
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="general",
                key=mid,
                content="内容内容内容内容内容",
                lane="general",
                status="active",
                decay_score=1.0,
                created_at=utcnow() - timedelta(days=days_ago),
            )
        )
        s.commit()
    return mid


def test_usage_returns_seven_days_with_zero_fill(engine):
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage(ctx=Principal(user_id="alice"))
    daily = res["daily_new"]
    assert len(daily) == 7  # 恰好 7 天，缺日补 0
    assert all(v >= 0 for v in daily.values())


def test_usage_counts_today(engine):
    from lantai.core.time import utcnow

    _add(engine)  # 今天（usage 按本地墙钟日聚合——usage.py:33 local_day_window_utc，
    # 票 32 裁决；断言必须用本地日期，UTC 日期在本地 00:00–07:59 与实现错位）
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage(ctx=Principal(user_id="alice"))
    today = utcnow().astimezone().strftime("%Y-%m-%d")
    assert today in res["daily_new"]
    # NULL 属主的老行对任何 viewer 可见（票 .scratch/mcp-identity-gaps/01b：
    # `== viewer OR IS NULL`，判不可见会让单人部署的报表归零）
    assert res["daily_new"][today] >= 1


def test_usage_ignores_old_records(engine):
    """8 天前的记忆不计入最近 7 天窗口"""
    _add(engine, days_ago=8.0)
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        res = usage(ctx=Principal(user_id="alice"))
    assert sum(res["daily_new"].values()) == 0
