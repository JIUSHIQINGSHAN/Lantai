"""PULSE 脉搏面板（Ticket 06）路由 + 端点冒烟测试。

/stats 聚合真实 DB 直调（不 mock）；页面只验证可达与契约引用。
"""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem


@pytest.fixture()
def pulse_env():
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


def _mem(i, **kw):
    base = dict(id=f"mem_{i}", memory_type="semantic", key=f"key_{i}",
                content=f"内容 {i}", lane="general", status="active",
                tier="working")
    base.update(kw)
    return MemoryItem(**base)


def test_ui_pulse_served():
    """页面可达：/ui/pulse 200 + 面板标记 + 三个数据端点引用。"""
    from fastapi.testclient import TestClient

    from api_server import app
    with TestClient(app) as c:
        r = c.get("/ui/pulse")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "脉搏" in r.text
        assert "/stats" in r.text
        assert "/usage" in r.text
        assert "/health/deep" in r.text


def test_stats_endpoint_aggregates(pulse_env):
    """真实 DB 聚合：/stats 返回总数 + 分层分布 + coalesce 水位结构。"""
    session_factory, _ = pulse_env
    with session_factory() as s:
        s.add(_mem(1, lane="general", tier="working"))
        s.add(_mem(2, lane="fact", tier="long_term"))
        s.add(_mem(3, lane="general", tier="working", status="archived"))
        s.commit()
    from fastapi.testclient import TestClient

    from api_server import app
    with TestClient(app) as c:
        r = c.get("/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_memories"] == 3
        assert body["by_lane"] == {"general": 2, "fact": 1}
        assert body["by_status"] == {"active": 2, "archived": 1}
        assert body["by_tier"] == {"working": 2, "long_term": 1}
        assert isinstance(body["coalesce_buffer"], dict)


def test_ui_index_includes_pulse():
    """入口页含三面板链接。"""
    from fastapi.testclient import TestClient

    from api_server import app
    with TestClient(app) as c:
        r = c.get("/ui")
        assert r.status_code == 200
        for path in ("/ui/recall", "/ui/evolve", "/ui/pulse"):
            assert path in r.text
