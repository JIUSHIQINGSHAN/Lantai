"""Native MCP attribution: real dispatch, SQLite, Chroma and ingestion; no core mocks."""

import copy
import json
import time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.cli import mcp
from lantai.core.attribution import ATTRIBUTION_META_KEY
from lantai.models.tables import MemoryCandidate, MemoryItem, RetrievalEvent

pytestmark = pytest.mark.mcp_full_surface()


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    from lantai.core.settings import settings
    from lantai.services import async_ingest_service
    from lantai.storage import db, vector_store
    from lantai.storage.fts import init_fts

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'attribution.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "get_session", lambda: Session(engine))
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "COALESCE_ENABLED", False)
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", True)
    monkeypatch.setattr(vector_store, "_store", None)
    monkeypatch.setattr(async_ingest_service, "_TASKS", {})
    monkeypatch.setattr(mcp, "_guard_instance", mcp.LoopGuard())
    yield lambda: Session(engine)
    engine.dispose()


def _call(name, args, meta=None):
    params = {"name": name, "arguments": args}
    if meta is not None:
        params["_meta"] = meta
    return mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})


def _result(frame):
    assert "result" in frame, frame
    return json.loads(frame["result"]["content"][0]["text"])


@pytest.mark.parametrize(
    "meta",
    [
        "private-body",
        [],
        {ATTRIBUTION_META_KEY: {"version": 2}},
        {ATTRIBUTION_META_KEY: {"user_id": "private-body"}},
        {ATTRIBUTION_META_KEY: {"request_id": "private-body\n"}},
    ],
)
def test_metadata_validation_safe_and_before_handler(sqlite_env, meta):
    result = _call("search", {"query": "hi"}, meta)
    assert result["error"]["code"] == -32602
    assert "private-body" not in json.dumps(result)
    with sqlite_env() as session:
        assert session.exec(select(RetrievalEvent)).all() == []


@pytest.mark.parametrize(
    "handler,args",
    [
        (mcp.handle_search, {"query": "hi"}),
        (mcp.handle_add, {"title": "note", "content": "记住：归因测试内容足够长"}),
        (mcp.handle_raw_add, {"content": "original text"}),
        (mcp.handle_add_dialogue, {"text": "hi"}),
        (mcp.handle_dialogue_add_async, {"text": "hi"}),
    ],
)
def test_direct_handlers_validate_untrusted_context(sqlite_env, handler, args):
    with pytest.raises(ValueError, match="attribution"):
        handler({**args, "attribution": {"user_id": "private-body"}})


def test_metadata_overrides_model_context_without_mutating_input(sqlite_env):
    args = {
        "query": "hi",
        "user_id": "alice",
        "session_id": "import-A",
        "attribution": {"user_id": "invalid-model-context"},
    }
    before = copy.deepcopy(args)
    frame = _call(
        "search",
        args,
        {
            ATTRIBUTION_META_KEY: {
                "version": 1,
                "request_session_id": "host-B",
                "request_id": "call-B",
            }
        },
    )
    result = _result(frame)
    with sqlite_env() as session:
        event = session.get(RetrievalEvent, result["event_id"])
        assert event.session_id == "import-A"
        assert event.request_id == "call-B"
        assert event.user_id == "alice"
    assert args == before
    assert (
        mcp._principal_from_params(
            {**args, "attribution": {"request_session_id": "host-B"}}
        ).session_id
        is None
    )


def test_search_fallback_logs_and_does_not_leak_into_next_call(sqlite_env):
    attributed = _result(
        _call(
            "search",
            {"query": "hi"},
            {ATTRIBUTION_META_KEY: {"request_session_id": "host-A", "request_id": "request-A"}},
        )
    )
    legacy = mcp.handle_search({"query": "hi"})
    with sqlite_env() as session:
        assert session.get(RetrievalEvent, attributed["event_id"]).session_id == "host-A"
        assert session.get(RetrievalEvent, attributed["event_id"]).request_id == "request-A"
        assert session.get(RetrievalEvent, legacy["event_id"]).session_id is None


def test_request_ids_cannot_bypass_loop_guard_and_sessions_are_distinct(sqlite_env):
    for index in range(5):
        frame = _call(
            "search",
            {"query": ""},
            {ATTRIBUTION_META_KEY: {"request_session_id": "host-A", "request_id": str(index)}},
        )
        assert frame["error"]["code"] == -32602
    frame = _call(
        "search",
        {"query": ""},
        {ATTRIBUTION_META_KEY: {"request_session_id": "host-A", "request_id": "next"}},
    )
    assert frame["error"]["code"] == -32005
    other_session = _call(
        "search",
        {"query": ""},
        {ATTRIBUTION_META_KEY: {"request_session_id": "host-B", "request_id": "next"}},
    )
    assert other_session["error"]["code"] == -32602
    assert mcp._fingerprint(
        "search", {"attribution": {"request_id": "A", "request_session_id": "B"}}
    ) == mcp._fingerprint("search", {"attribution": {"request_id": "C", "request_session_id": "B"}})
    assert mcp._fingerprint("search", {"session_id": "A"}) != mcp._fingerprint(
        "search", {"session_id": "B"}
    )
    assert mcp._fingerprint("search", {"metadata": {"request_id": "A"}}) != mcp._fingerprint(
        "search", {"metadata": {"request_id": "B"}}
    )


def test_supported_tool_schemas_expose_origin_and_turn():
    for name in ("add", "raw_add", "add_dialogue", "dialogue_add_async"):
        assert mcp.TOOLS[name]["inputSchema"]["properties"]["session_id"]["type"] == "string"
    for name in ("add_dialogue", "dialogue_add_async"):
        assert mcp.TOOLS[name]["inputSchema"]["properties"]["turn"]["type"] == "integer"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("add", {"title": "attribution", "content": "记住：我喜欢蓝色主题和简洁的界面"}),
        ("raw_add", {"content": "Original attribution test text for raw storage."}),
        ("add_dialogue", {"text": "记住：我喜欢蓝色主题和简洁的界面", "turn": 9}),
    ],
)
@pytest.mark.parametrize("origin", ["import-A", ""])
def test_ingest_dispatch_persists_context_and_explicit_origin(sqlite_env, tool, args, origin):
    from lantai.models.tables import IngestEvent

    result = _result(
        _call(
            tool,
            {**args, "session_id": origin},
            {
                ATTRIBUTION_META_KEY: {
                    "version": 1,
                    "request_session_id": "host-B",
                    "request_id": "call-B",
                }
            },
        )
    )
    with sqlite_env() as session:
        event = session.get(IngestEvent, result["ingest_event_id"])
        assert event.session_id == (origin or "host-B")
        assert event.request_session_id == "host-B"
        assert event.request_id == "call-B"
        assert event.attribution_state == ("conflict" if origin else "fallback")
        row = (
            session.get(MemoryItem, result["memory_id"])
            if tool == "raw_add"
            else session.get(MemoryCandidate, result["candidate_id"])
        )
        assert row.session_id == (origin or "host-B")
        if tool == "add_dialogue":
            assert row.provenance["origin_turn"] == 9


def test_async_dispatch_preserves_turn_and_idempotency(sqlite_env):
    from lantai.models.tables import IngestEvent
    from lantai.services.async_ingest_service import get_task_status

    args = {
        "text": "记住：我的测试项目采用独立 SQLite 数据库",
        "session_id": "import-A",
        "turn": 3,
        "idempotency_key": "mcp-attribution-async",
    }
    meta = {ATTRIBUTION_META_KEY: {"request_session_id": "host-B", "request_id": "call-async"}}
    result = _result(_call("dialogue_add_async", args, meta))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = get_task_status(result["task_id"])
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert status["status"] == "completed", status
    replay = _result(_call("dialogue_add_async", args, meta))
    assert replay["task_id"] == result["task_id"]
    with sqlite_env() as session:
        events = session.exec(select(IngestEvent)).all()
        assert len(events) == 1
        assert events[0].session_id == "import-A"
        assert events[0].request_session_id == "host-B"
        candidate = session.get(MemoryCandidate, status["result"]["candidate_id"])
        assert candidate.session_id == "import-A"
        assert candidate.provenance["origin_turn"] == 3
