"""删除路由归属校验（P0 票04）测试：真 DB + 真 HTTP，不 mock 内部逻辑。

覆盖：非归属 403（资源原样）、归属成功、admin 跨归属成功、lane 越权 403、
无归属资源（历史行 user_id 为空）非 admin 可删（只受 lane 约束）。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryEdge, MemoryItem, RawDocument


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        "lantai.storage.vector_store.get_vector_store",
        lambda: type("D", (), {"delete": lambda *a, **k: None})(),
    )
    with TestClient(app) as client:
        yield engine, client


def _principal(user_id="u1", role="user", lanes=("general",), tenant=None):
    return Principal(user_id=user_id, role=role, allowed_lanes=list(lanes), tenant_id=tenant)


def _override(client, principal):
    app.dependency_overrides[get_current_user] = lambda: principal


def _teardown_override():
    app.dependency_overrides.pop(get_current_user, None)


def _add_memory(engine, *, id="mem_o1", user_id="u1", tenant=None, lane="general"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=id,
                memory_type="semantic",
                key=f"k-{id}",
                content=f"content-{id}",
                lane=lane,
                user_id=user_id,
                tenant_id=tenant,
                status="active",
            )
        )
        s.commit()


def _memory_exists(engine, memory_id):
    with Session(engine) as s:
        return s.get(MemoryItem, memory_id) is not None


class TestTerminalMemoryDelete:
    def test_other_user_forbidden_and_resource_intact(self, env):
        engine, client = env
        _add_memory(engine, id="mem_ou", user_id="someone-else")
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/terminal/memory/mem_ou")
            assert resp.status_code == 403, resp.text
            assert _memory_exists(engine, "mem_ou")  # 资源原样
        finally:
            _teardown_override()

    def test_owner_can_delete(self, env):
        engine, client = env
        _add_memory(engine, id="mem_own", user_id="u1")
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/terminal/memory/mem_own")
            assert resp.status_code == 200, resp.text
            assert not _memory_exists(engine, "mem_own")
        finally:
            _teardown_override()

    def test_admin_cross_user_allowed(self, env):
        engine, client = env
        _add_memory(engine, id="mem_admin", user_id="someone-else")
        try:
            _override(client, _principal(user_id="boss", role="admin"))
            resp = client.delete("/terminal/memory/mem_admin")
            assert resp.status_code == 200, resp.text
            assert not _memory_exists(engine, "mem_admin")
        finally:
            _teardown_override()

    def test_lane_outside_allowed_forbidden(self, env):
        engine, client = env
        _add_memory(engine, id="mem_lane", user_id="u1", lane="wiki")
        try:
            _override(client, _principal(user_id="u1", lanes=("general",)))
            resp = client.delete("/terminal/memory/mem_lane")
            assert resp.status_code == 403, resp.text
            assert _memory_exists(engine, "mem_lane")
        finally:
            _teardown_override()

    def test_unowned_resource_deletable_by_non_admin(self, env):
        """历史行无 user_id：不视为越权，非 admin 可删（lane 约束仍生效）。"""
        engine, client = env
        _add_memory(engine, id="mem_legacy", user_id=None)
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/terminal/memory/mem_legacy")
            assert resp.status_code == 200, resp.text
        finally:
            _teardown_override()


class TestEdgesAndDocuments:
    def test_edge_other_user_forbidden(self, env):
        engine, client = env
        with Session(engine) as s:
            s.add(
                MemoryEdge(
                    id="edge_o1",
                    source_memory_id="a",
                    target_memory_id="b",
                    relation="supports",
                    user_id="someone-else",
                )
            )
            s.commit()
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/edges/edge_o1")
            assert resp.status_code == 403, resp.text
            with Session(engine) as s:
                assert s.get(MemoryEdge, "edge_o1") is not None
        finally:
            _teardown_override()

    def test_edge_owner_can_delete(self, env):
        engine, client = env
        with Session(engine) as s:
            s.add(
                MemoryEdge(
                    id="edge_o2",
                    source_memory_id="a",
                    target_memory_id="b",
                    relation="supports",
                    user_id="u1",
                )
            )
            s.commit()
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/edges/edge_o2")
            assert resp.status_code == 200, resp.text
        finally:
            _teardown_override()

    def test_document_other_user_forbidden(self, env):
        engine, client = env
        with Session(engine) as s:
            s.add(
                RawDocument(
                    id="doc_o1",
                    source_type="dialogue",
                    source_id="t",
                    url="",
                    title="t",
                    content="c",
                    content_hash="h-o1",
                    user_id="someone-else",
                )
            )
            s.commit()
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/documents/doc_o1")
            assert resp.status_code == 403, resp.text
            with Session(engine) as s:
                assert (
                    s.exec(select(RawDocument).where(RawDocument.id == "doc_o1")).first()
                    is not None
                )
        finally:
            _teardown_override()

    def test_document_owner_can_delete(self, env):
        engine, client = env
        with Session(engine) as s:
            s.add(
                RawDocument(
                    id="doc_o2",
                    source_type="dialogue",
                    source_id="t",
                    url="",
                    title="t",
                    content="c",
                    content_hash="h-o2",
                    user_id="u1",
                )
            )
            s.commit()
        try:
            _override(client, _principal(user_id="u1"))
            resp = client.delete("/documents/doc_o2")
            assert resp.status_code == 200, resp.text
        finally:
            _teardown_override()
