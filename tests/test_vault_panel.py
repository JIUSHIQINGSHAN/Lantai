"""VAULT 档案控制台（Ticket 06）测试。

build_memories_page 纯函数真实临时 SQLite 直调（不 mock）；页面/端点冒烟。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine


@pytest.fixture()
def vault_env():
    """内存 SQLite 真实建表 + patch 仅 db.get_session。"""
    import lantai.models.tables  # noqa
    import lantai.storage.db as db_module
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _mem(s, i, content, lane="fact", status="active", decay_class="episodic",
         updated=None, memory_type="semantic"):
    from lantai.models.tables import MemoryItem
    now = datetime.now(timezone.utc)
    m = MemoryItem(
        id=f"mem_{i}", memory_type=memory_type, key=f"k{i}",
        content=content, lane=lane, status=status,
        importance=0.5, decay_score=1.0, decay_class=decay_class,
        use_count=i,
        created_at=now - timedelta(days=10),
        updated_at=updated if updated is not None else (now - timedelta(hours=i)),
    )
    s.add(m)


def test_build_memories_page_orders_and_filters(vault_env):
    """纯函数：updated_at 新→旧排序；lane/status/decay_class 过滤。"""
    session_factory, _ = vault_env
    now = datetime.now(timezone.utc)
    with session_factory() as s:
        _mem(s, 1, "记忆甲", lane="fact", updated=now - timedelta(hours=3))
        _mem(s, 2, "记忆乙", lane="rule", status="archived",
             decay_class="procedural", updated=now)
        _mem(s, 3, "记忆丙", lane="fact", decay_class="semantic",
             updated=now - timedelta(hours=1))
        s.commit()
    from lantai.services.memory_service import build_memories_page
    with session_factory() as s:
        page = build_memories_page(s)
        assert [m["id"] for m in page["memories"]] == ["mem_2", "mem_3", "mem_1"]
        assert page["total"] == 3

        f_lane = build_memories_page(s, lane="fact")
        assert [m["id"] for m in f_lane["memories"]] == ["mem_3", "mem_1"]

        f_status = build_memories_page(s, status="archived")
        assert [m["id"] for m in f_status["memories"]] == ["mem_2"]

        f_decay = build_memories_page(s, decay_class="semantic")
        assert [m["id"] for m in f_decay["memories"]] == ["mem_3"]


def test_build_memories_page_pagination_and_truncation(vault_env):
    """纯函数：limit/offset 分页 + total；content 截断带省略号。"""
    session_factory, _ = vault_env
    with session_factory() as s:
        for i in range(25):
            _mem(s, i, "内容" * 40 + str(i))
        s.commit()
    from lantai.services.memory_service import build_memories_page
    with session_factory() as s:
        page1 = build_memories_page(s, limit=10, offset=0)
        assert page1["total"] == 25 and len(page1["memories"]) == 10
        assert page1["offset"] == 0

        page3 = build_memories_page(s, limit=10, offset=20)
        assert len(page3["memories"]) == 5

        short = build_memories_page(s, limit=1, content_max=8)
        assert short["memories"][0]["content"].endswith("…")
        assert len(short["memories"][0]["content"]) == 9  # 8 字符 + 省略号


def test_build_memories_page_validation(vault_env):
    """纯函数：limit/offset/content_max 越界抛 ValueError。"""
    session_factory, _ = vault_env
    from lantai.services.memory_service import build_memories_page
    with session_factory() as s:
        with pytest.raises(ValueError):
            build_memories_page(s, limit=0)
        with pytest.raises(ValueError):
            build_memories_page(s, limit=101)
        with pytest.raises(ValueError):
            build_memories_page(s, offset=-1)
        with pytest.raises(ValueError):
            build_memories_page(s, content_max=-1)


def test_ui_vault_served():
    """页面可达：/ui/vault 200 + 面板标记 + 数据端点引用。"""
    from fastapi.testclient import TestClient
    from api_server import app
    with TestClient(app) as c:
        r = c.get("/ui/vault")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "档案与锦囊" in r.text
        assert "/memories" in r.text
        assert "/candidates/pending" in r.text


def test_memories_endpoint(vault_env):
    """数据端点：/memories 返回分页结构 + 过滤生效。"""
    session_factory, _ = vault_env
    with session_factory() as s:
        _mem(s, 1, "事实记忆", lane="fact")
        _mem(s, 2, "规则记忆", lane="rule")
        s.commit()
    from fastapi.testclient import TestClient
    from api_server import app
    with TestClient(app) as c:
        r = c.get("/memories?lane=fact&limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["memories"][0]["lane"] == "fact"
        assert "content" in body["memories"][0]
        bad = c.get("/memories?limit=0")
        assert bad.status_code == 422
