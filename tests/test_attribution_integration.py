"""Actual REST routes, SQLite/FTS and Chroma integration; only LLM network is faked."""

import sqlite3
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.auth import Principal, get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import IngestEvent, MemoryCandidate, MemoryItem, RetrievalEvent
from lantai.storage import db


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    from lantai.core.settings import settings
    from lantai.storage import vector_store
    from lantai.storage.fts import init_fts

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'pipeline.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "vectors"))
    monkeypatch.setattr(vector_store, "_store", None)
    monkeypatch.setattr(settings, "COALESCE_ENABLED", False)
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", True)
    yield engine
    engine.dispose()


def test_raw_dedup_has_two_events_and_does_not_replace_origin(real_db):
    from lantai.models.schemas import RawMemoryReq
    from lantai.services.memory_service import add_raw_memory

    body = "This exact public source is submitted by two conversations."
    first = add_raw_memory(RawMemoryReq(content=body, session_id="original"))
    second = add_raw_memory(
        RawMemoryReq(content=body, attribution={"request_session_id": "second"})
    )
    assert second["dedup"] is True
    assert first["memory_id"] == second["memory_id"]
    with Session(real_db) as session:
        rows = session.exec(select(IngestEvent)).all()
        assert len(rows) == 2
        assert {r.outcome for r in rows} == {"created", "reinforced"}
        assert {r.session_id for r in rows} == {"original", "second"}
        assert {r.memory_id for r in rows} == {first["memory_id"]}
        assert session.get(MemoryItem, first["memory_id"]).session_id == "original"


def test_rest_body_attribution_does_not_become_principal_scope(real_db):
    from lantai.api.routes_dialogue import router as dialogue_router
    from lantai.api.routes_memory import router as memory_router
    from lantai.api.routes_search import router as search_router

    app = FastAPI()
    app.include_router(dialogue_router)
    app.include_router(memory_router)
    app.include_router(search_router)
    principal = Principal(
        user_id="alice", tenant_id="tenant-A", agent_id="agent-A", allowed_lanes=["general"]
    )
    app.dependency_overrides[get_current_user] = lambda: principal
    with TestClient(app) as client:
        result = client.post(
            "/dialogue",
            json={
                "text": "记住：我的测试服务使用蓝色主题",
                "session_id": "history",
                "attribution": {"request_session_id": "current", "request_id": "call-1"},
            },
        )
        assert result.status_code == 200, result.text
        first = client.post(
            "/add/raw", json={"content": "First raw entry for listing check.", "session_id": "A"}
        ).json()
        second = client.post(
            "/add/raw", json={"content": "Second raw entry for listing check.", "session_id": "B"}
        ).json()
        listing = client.get("/memories").json()
        assert listing["total"] == 2
        search = client.post(
            "/search",
            json={
                "query": "hi",
                "attribution": {"request_session_id": "current", "request_id": "call-2"},
            },
        )
        assert search.status_code == 200, search.text
        legacy = client.post("/search", json={"query": "hi"})
        assert legacy.status_code == 200
        assert principal.session_id is None
        assert client.get("/memories").json()["total"] == 2
    with Session(real_db) as session:
        event = session.get(IngestEvent, result.json()["ingest_event_id"])
        assert (event.session_id, event.request_session_id, event.attribution_state) == (
            "history",
            "current",
            "conflict",
        )
        assert (event.user_id, event.tenant_id, event.agent_id) == ("alice", "tenant-A", "agent-A")
        read = session.get(RetrievalEvent, search.json()["event_id"])
        assert (read.session_id, read.request_id, read.agent_id) == ("current", "call-2", "agent-A")
        assert session.get(RetrievalEvent, legacy.json()["event_id"]).session_id is None
        assert session.get(MemoryItem, first["memory_id"]).session_id == "A"
        assert session.get(MemoryItem, second["memory_id"]).session_id == "B"


def test_coalesced_batch_associates_all_events_and_retry_only_one(real_db, monkeypatch):
    from lantai.core.settings import settings
    from lantai.ingestion.coalesce import CoalesceBuffer
    from lantai.models.schemas import AddMemoryReq
    from lantai.services import memory_service

    profiles = dict(settings.LANE_COALESCE_PROFILES)
    profiles["general"] = {"max_parts": 2, "max_chars": 99999, "window": 1000, "idle_timeout": 1000}
    monkeypatch.setattr(settings, "LANE_COALESCE_PROFILES", profiles)
    monkeypatch.setattr(settings, "COALESCE_ENABLED", True)
    buffer = CoalesceBuffer()
    monkeypatch.setattr(memory_service, "get_coalesce_buffer", lambda: buffer)
    req = AddMemoryReq(
        title="one",
        content="A neutral description of the first part.",
        attribution={"request_session_id": "A"},
    )
    first = memory_service.add_memory_async(req, user_id="alice")
    retry = memory_service.add_memory_async(req, user_id="alice")
    assert retry.get("duplicate")
    assert retry["ingest_event_id"] == first["ingest_event_id"]
    second = memory_service.add_memory_async(
        req.model_copy(update={"content": "A neutral description of the second part."}),
        user_id="alice",
    )
    assert second["status"] == "flushed"
    with Session(real_db) as session:
        events = session.exec(select(IngestEvent)).all()
        assert len(events) == 2
        assert {e.candidate_id for e in events} == {second["candidate_id"]}
        assert {e.outcome for e in events} == {"candidate"}
        assert {e.session_id for e in events} == {"A"}


def test_coalesce_actor_and_colon_ids_do_not_mix(monkeypatch):
    from lantai.core.settings import settings
    from lantai.ingestion.coalesce import CoalesceBuffer

    profiles = dict(settings.LANE_COALESCE_PROFILES)
    profiles["general"] = {"max_parts": 10, "max_chars": 99999, "window": 1000, "idle_timeout": -1}
    monkeypatch.setattr(settings, "LANE_COALESCE_PROFILES", profiles)
    buffer = CoalesceBuffer()
    for tenant, agent, sid in [("t1", "a", "s:x"), ("t2", "a", "s:x"), ("t1", "b", "s:x")]:
        buffer.add("u", "general", tenant + agent, session_id=sid, tenant_id=tenant, agent_id=agent)
    flushed = buffer.check_idle()
    assert len(flushed) == 3
    assert {(r["tenant_id"], r["agent_id"], r["session_id"]) for r in flushed} == {
        ("t1", "a", "s:x"),
        ("t2", "a", "s:x"),
        ("t1", "b", "s:x"),
    }


def test_coalesce_retry_fingerprint_has_unambiguous_field_boundaries():
    from lantai.ingestion.coalesce import CoalesceBuffer

    buffer = CoalesceBuffer()
    first = buffer.add_async("a", "b", "d:e", session_id="c")
    second = buffer.add_async("a:b", "c", "e", session_id="d")
    assert first["job_id"] != second["job_id"]
    assert not second.get("duplicate")
    assert buffer.water_level()["total_messages"] == 2


def test_v46_migration_keeps_existing_rows_and_does_not_guess_origin(tmp_path):
    from lantai.storage.migrations import _migrate_v46

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE memoryitem(id TEXT PRIMARY KEY, session_id TEXT, content TEXT)")
        conn.execute("INSERT INTO memoryitem VALUES ('old', NULL, 'untouched')")
        _migrate_v46(conn)
        _migrate_v46(conn)
        assert conn.execute("SELECT * FROM memoryitem").fetchall() == [("old", None, "untouched")]
        assert conn.execute("SELECT count(*) FROM ingest_event").fetchone()[0] == 0
        fields = {r[1] for r in conn.execute("PRAGMA table_info(ingest_event)")}
        assert fields == set(IngestEvent.model_fields)
