"""终端写路由守卫（现状整改票 03）：PUT 归属校验 + PATCH retracted 守卫。

真 DB + 真 FTS + 真 HTTP，不 mock 内部逻辑；替身仅限向量库/embed 外部面。
FTS 断言走真实检索面 search_fts（不裸查虚表）。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import get_current_user
from lantai.models.tables import MemoryItem


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    added: list[str] = []
    dummy = type(
        "D",
        (),
        {
            "add": lambda self, *, ids, embeddings, metadatas: added.extend(ids),
            "delete": lambda self, *a, **k: None,
        },
    )()
    monkeypatch.setattr("lantai.storage.vector_store.get_vector_store", lambda: dummy)
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: dummy)
    import lantai.services.record_ops_service as ros

    monkeypatch.setattr(ros, "embed", lambda texts: [[0.1] * 1536 for _ in texts])

    with TestClient(app) as client:
        yield engine, client, added


def _principal(user_id="u1", role="user", lanes=("general",), tenant=None):
    from lantai.core.auth import Principal

    return Principal(user_id=user_id, role=role, allowed_lanes=list(lanes), tenant_id=tenant)


def _override(client, principal, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: principal)


def _add_memory(engine, *, id="mem_g1", user_id="u1", lane="general", status="active"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=id,
                memory_type="semantic",
                key=f"k-{id}",
                content=f"content-{id}",
                lane=lane,
                user_id=user_id,
                status=status,
            )
        )
        s.commit()


def _memory(engine, memory_id):
    with Session(engine) as s:
        m = s.get(MemoryItem, memory_id)
        return m.content, m.status


def _fts_hits(engine, query):
    from lantai.storage.fts import search_fts

    with engine.connect() as conn:
        return search_fts(conn.connection.driver_connection, query, top_k=50)


def test_put_other_user_forbidden_and_content_intact(env, monkeypatch):
    engine, client, _ = env
    _add_memory(engine, id="mem_ou", user_id="someone-else")
    _override(client, _principal(user_id="u1"), monkeypatch)
    resp = client.put("/terminal/memory/mem_ou", json={"content": "篡改正文"})
    assert resp.status_code == 403, resp.text
    assert _memory(engine, "mem_ou") == ("content-mem_ou", "active")


def test_put_lane_outside_allowed_forbidden(env, monkeypatch):
    engine, client, _ = env
    _add_memory(engine, id="mem_lr", lane="rule")
    _override(client, _principal(user_id="u1", lanes=("general",)), monkeypatch)
    resp = client.put("/terminal/memory/mem_lr", json={"importance": 0.9})
    assert resp.status_code == 403, resp.text


def test_put_owner_active_ok_and_fts_synced(env, monkeypatch):
    engine, client, added = env
    _add_memory(engine, id="mem_ok")
    _override(client, _principal(user_id="u1"), monkeypatch)
    resp = client.put("/terminal/memory/mem_ok", json={"content": "新正文"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["vector_synced"] is True
    assert _memory(engine, "mem_ok") == ("新正文", "active")
    assert len(_fts_hits(engine, "新正文")) >= 1  # 正面对照：FTS 同步在本 harness 真实生效
    assert added == ["mem_ok"]


def test_put_admin_cross_owner_ok(env, monkeypatch):
    engine, client, _ = env
    _add_memory(engine, id="mem_ac", user_id="someone-else")
    _override(client, _principal(user_id="boss", role="admin"), monkeypatch)
    resp = client.put("/terminal/memory/mem_ac", json={"confidence": 0.8})
    assert resp.status_code == 200, resp.text


def test_patch_rejected_retracted_and_index_untouched(env, monkeypatch):
    engine, client, added = env
    _add_memory(engine, id="mem_rt", status="retracted")
    _override(client, _principal(user_id="u1"), monkeypatch)
    resp = client.put("/terminal/memory/mem_rt", json={"content": "复活正文"})
    assert resp.status_code == 409, resp.text
    assert _memory(engine, "mem_rt") == ("content-mem_rt", "retracted")
    assert _fts_hits(engine, "复活正文") == []  # 撤回清空面不被改文旁路重新填回（D23）
    assert added == []
