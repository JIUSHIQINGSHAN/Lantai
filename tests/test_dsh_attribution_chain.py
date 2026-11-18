"""Installed DSH adapter -> SDK/stdio -> Python HTTP bridge -> REST -> real SQLite.

Only available when the installed Electron/DSH module root is explicitly selected.
The Host execution objects and authenticated Principal are synthetic; no GUI turn,
profile, production configuration, or live authentication service is exercised.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import IngestEvent, MemoryCandidate, MemoryItem, RetrievalEvent


@pytest.fixture
def chain_backend(tmp_path, monkeypatch):
    from lantai.api.routes_dialogue import router as dialogue_router
    from lantai.api.routes_memory import router as memory_router
    from lantai.api.routes_search import router as search_router
    from lantai.core.settings import settings
    from lantai.storage import db, vector_store
    from lantai.storage.fts import init_fts

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chain.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "vectors"))
    monkeypatch.setattr(vector_store, "_store", None)
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", True)
    monkeypatch.setattr(settings, "COALESCE_ENABLED", False)
    app = FastAPI()
    for router in (dialogue_router, memory_router, search_router):
        app.include_router(router)
    principal = Principal(
        user_id="wire-user",
        tenant_id="wire-tenant",
        agent_id="wire-agent",
        allowed_lanes=["general"],
    )
    app.dependency_overrides[get_current_user] = lambda: principal
    scope_headers = []

    @app.middleware("http")
    async def record_scope_header(request, call_next):
        scope_headers.append("x-session-id" in request.headers)
        return await call_next(request)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            log_level="critical",
            access_log=False,
            lifespan="off",
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                pytest.fail("isolated loopback REST server did not start")
            time.sleep(0.01)
        yield engine, port, principal, scope_headers
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        engine.dispose()
        assert not thread.is_alive(), "isolated REST server did not stop"


def test_installed_dsh_stdio_rest_sqlite_chain(tmp_path, request):
    if not os.environ.get("DSH_NODE_EXECUTABLE") or not os.environ.get("DSH_INSTALL_DIR"):
        pytest.skip(
            "select DSH_NODE_EXECUTABLE and DSH_INSTALL_DIR for installed chain verification"
        )
    electron = os.environ["DSH_NODE_EXECUTABLE"]
    # Request the backend only after checking prerequisites; a skipped test starts no services.
    engine, port, principal, scope_headers = request.getfixturevalue("chain_backend")
    root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "result.json"
    trace_path = tmp_path / "trace.txt"
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "python": sys.executable,
                "child": str(root / "tests/support/dsh_bridge_child.py"),
                "bridge": str(root / "scripts/lantai_mcp_bridge.py"),
                "port": port,
                "result": str(result_path),
                "trace": str(trace_path),
            }
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    try:
        process = subprocess.run(
            [
                electron,
                str(root / "integrations/dsh-lantai/tests/run-installed.cjs"),
                "chain-client.js",
                str(fixture_path),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            # Electron cold start plus real SQLite/FTS/Chroma setup can exceed
            # 30s on a loaded Windows host; timeout still bounds the smoke.
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else "<no stage>"
        stdout = (
            (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            (exc.stderr or b"").decode("utf-8", "replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        pytest.fail(
            f"installed DSH chain timed out after {exc.timeout}s; stage={trace}; "
            f"stdout={stdout[-1000:]!r}; stderr={stderr[-1000:]!r}",
            pytrace=False,
        )
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    with Session(engine) as session:
        events = session.exec(select(IngestEvent)).all()
        by_call = {row.request_id: row for row in events}
        assert len(events) == 7
        for call, sid, turn, response in (
            ("dialogue-A", "wire-A", 1, result["a"]),
            ("dialogue-B", "wire-B", 2, result["b"]),
            ("history", "historical-origin", 3, result["history"]),
        ):
            row = by_call[call]
            assert row.id == response["ingest_event_id"]
            assert row.session_id == sid
            assert row.attribution_state == ("conflict" if call == "history" else "fallback")
            assert row.request_session_id == ("wire-B" if call == "dialogue-B" else "wire-A")
            candidate = session.get(MemoryCandidate, row.candidate_id)
            assert candidate.session_id == sid
            assert candidate.provenance["origin_turn"] == turn
            assert candidate.provenance["origin_session_id"] == sid
            assert row.outcome == "candidate"
        assert by_call["agentless"].session_id is None
        assert by_call["agentless"].request_session_id is None
        assert by_call["agentless"].attribution_state == "missing"
        assert by_call["agentless"].outcome == "rejected"
        assert by_call["raw-A"].outcome == "created"
        assert by_call["raw-B"].outcome == "reinforced"
        assert by_call["raw-A"].memory_id == by_call["raw-B"].memory_id
        memory = session.get(MemoryItem, by_call["raw-A"].memory_id)
        assert memory.session_id == "wire-A"
        assert by_call["raw-B"].session_id == "wire-B"
        assert "invalid-body" not in by_call  # REST validation rejects before reception.
        assert by_call["rejected-input"].outcome == "rejected"
        assert by_call["rejected-input"].reason_code == "input_rejected"
        assert {(row.user_id, row.tenant_id, row.agent_id) for row in events} == {
            ("wire-user", "wire-tenant", "wire-agent")
        }
        read = session.get(RetrievalEvent, result["read"]["event_id"])
        assert (read.session_id, read.request_id) == ("wire-B", "search-B")
    assert principal.session_id is None
    assert scope_headers and not any(scope_headers)
