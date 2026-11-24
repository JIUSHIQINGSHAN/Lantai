"""边界锁：尘封检索的 `limit`/`offset` 不得越界，尤其不得传负 `limit`。

SQLite 的 `LIMIT -1` 是**无上限**：`{"limit": -1}` 会把全部归档记忆的
`model_dump` 全文一次拉回。修前 REST 是裸 `int = 50`、MCP 是裸
`int(params.get(...))`、引擎直接 `stmt.limit(limit)`——三层都没有界。

本文件锁三层：引擎拒绝、REST 422、MCP ValueError（分发层译 -32602）。
不 mock 产品逻辑：真实内存库 + 真归档行。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


@pytest.fixture
def archived_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    now = utcnow()
    with Session(engine) as s:
        for i in range(3):
            s.add(
                MemoryItem(
                    id=f"mem-arch-{i}",
                    key=f"k{i}",
                    content=f"已归档的旧记录 {i}",
                    lane="general",
                    status="archived",
                    importance=0.4,
                    confidence=1.0,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
    return engine


@pytest.mark.parametrize("bad", [-1, -100, 0, 501, True, "10"])
def test_engine_rejects_out_of_range(archived_env, bad):
    """引擎层拒绝越界（含 bool——`True` 是 int 子类，不得当 1 混入）。"""
    from lantai.cognition.chenfeng import ChenfengEngine

    with Session(archived_env) as s:
        engine = ChenfengEngine(session=s)
        with pytest.raises(ValueError, match="limit"):
            engine.search_archived(limit=bad)


def test_engine_rejects_negative_offset(archived_env):
    from lantai.cognition.chenfeng import ChenfengEngine

    with Session(archived_env) as s:
        engine = ChenfengEngine(session=s)
        with pytest.raises(ValueError, match="offset"):
            engine.search_archived(offset=-5)


def test_engine_still_returns_within_range(archived_env):
    """反向保险：合法边界照常工作，且真的分页（不是靠拒绝把所有调用打死）。"""
    from lantai.cognition.chenfeng import ChenfengEngine

    with Session(archived_env) as s:
        engine = ChenfengEngine(session=s)
        page1 = engine.search_archived(limit=2, offset=0)
        page2 = engine.search_archived(limit=2, offset=2)
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    assert page1["total"] == 3


def test_rest_route_declares_bounds(archived_env):
    """REST 面的 Query 声明生效：limit=-1 直接 422（不进引擎）。"""
    from fastapi.testclient import TestClient

    from lantai.api.app import app

    with TestClient(app) as client:
        resp = client.get("/memories/archived", params={"limit": -1})
    assert resp.status_code == 422, f"REST 未拦越界 limit：{resp.status_code}"


def test_mcp_handler_rejects_bad_limit(archived_env):
    """MCP 面先验后用（分发层把 ValueError 译成 -32602）。"""
    from lantai.cli.mcp import handle_memory_archived_search

    with pytest.raises(ValueError, match="limit"):
        handle_memory_archived_search({"limit": -1})
