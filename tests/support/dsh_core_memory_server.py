"""Disposable local Lantai CoreMemoryBlock API used only by integration tests.

The parent owns a fresh TemporaryDirectory and launches this file in a separate
Python process. The child installs every storage/model setting before importing
any lantai module, then mounts the actual routes_memory router with real Bearer
key lookup against its own SQLite database.
"""

import argparse
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TOKEN = "fixture-token"
FIXTURE_USER_ID = "fixture-user"
FIXTURE_NAMESPACE = "fixture"
FIXTURE_AGENT_ID = "fixture-agent"
FIXTURE_POLICY = "fixture-real-policy-v1"
FIXTURE_IDENTITY = "Synthetic fixture identity data only."
FIXTURE_TASK = "Synthetic fixture task data only."
MAX_CHILD_LIFETIME_SECONDS = 120
MAX_FIXTURE_CONTENT_BYTES = 16 * 1024


def _fixture_environment(home: Path) -> dict[str, str]:
    """Make a strict env for the child before it can import lantai.settings/db."""
    db_file = home / "fixture.sqlite3"
    db_url = "sqlite:///" + db_file.as_posix()
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "LANTAI_HOME": str(home),
            "REMEMBRANCE_HOME": "",
            "DATABASE_URL": db_url,
            "HOST": "127.0.0.1",
            "PORT": "0",
            "API_KEY": "",
            "OPENAI_API_KEY": "fixture-only-not-a-credential",
            "OPENAI_BASE_URL": "https://fixture.invalid/v1",
            "LLM_MODEL": "fixture-no-network",
            "RERANKER_ENABLED": "false",
            "RERANKER_BASE_URL": "https://fixture.invalid/v1",
            "RERANKER_API_KEY": "fixture-only-not-a-credential",
            "LANTAI_RUN_SCHEDULER": "false",
            "MONITOR_ENABLED": "false",
        }
    )
    return env


class IsolatedCoreMemoryServer:
    """Context-managed real HTTP process, temp DB, and synthetic control surface."""

    def __init__(self, startup_timeout: float = 30.0) -> None:
        """Start a synthetic loopback API and wait at most startup_timeout seconds."""
        self._temporary = tempfile.TemporaryDirectory(prefix="lantai-core-memory-fixture-")
        self.home = Path(self._temporary.name).resolve()
        self.token = FIXTURE_TOKEN
        self.user_id = FIXTURE_USER_ID
        self.namespace = FIXTURE_NAMESPACE
        self.control_token = secrets.token_urlsafe(24)
        self.base_url = ""
        self.control_url = ""
        self._process: subprocess.Popen[str] | None = None
        self._closed = False
        self._reader_queue: queue.Queue[str | None] = queue.Queue()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--home",
            str(self.home),
            "--control-token",
            self.control_token,
        ]
        self._process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=_fixture_environment(self.home),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_child_lines, daemon=True)
        self._reader.start()
        deadline = time.monotonic() + startup_timeout
        seen: list[str] = []
        try:
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    line = self._reader_queue.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    if self._process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                seen.append(line.rstrip())
                try:
                    ready = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ready.get("event") == "ready":
                    self.base_url = str(ready["base_url"])
                    parsed = urlsplit(self.base_url)
                    self.control_url = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
                    if not self.base_url.startswith("http://127.0.0.1:") or not parsed.port:
                        raise RuntimeError("fixture child returned an unexpected loopback address")
                    break
            if not self.base_url:
                raise RuntimeError(
                    "isolated Lantai fixture did not become ready: " + " | ".join(seen[-8:])
                )
        except BaseException:
            self.close()
            raise

    def _read_child_lines(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for line in self._process.stdout:
                self._reader_queue.put(line)
        finally:
            self._reader_queue.put(None)

    def _control_post(self, path: str, payload: dict | None = None, timeout: float = 3.0) -> dict:
        if not self.base_url:
            raise RuntimeError("fixture server is not ready")
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.control_url + "__fixture__/" + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Fixture-Control": self.control_token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"synthetic fixture control returned HTTP {exc.code}: {detail}"
            ) from exc

    def update(self, block: str, content: str) -> dict:
        """Update one synthetic block through the child’s real memory_service."""
        if block not in {"policy", "identity", "task"}:
            raise ValueError("fixture control accepts only policy, identity, or task")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FIXTURE_CONTENT_BYTES:
            raise ValueError("fixture content exceeds its synthetic-data budget")
        return self._control_post("update", {"block": block, "content": content})

    def stop(self) -> None:
        """Request graceful shutdown, then release the child and temporary storage."""
        if self._closed:
            return
        try:
            if self._process is not None and self._process.poll() is None and self.base_url:
                self._control_post("stop", timeout=2.0)
        except Exception:
            pass
        self.close()

    def close(self) -> None:
        """Idempotently reap the bounded child process and remove its owned temp home."""
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if self.home.parent != Path(
            tempfile.gettempdir()
        ).resolve() or not self.home.name.startswith("lantai-core-memory-fixture-"):
            raise RuntimeError("refusing to clean a path outside the owned fixture directory")
        for attempt in range(6):
            try:
                self._temporary.cleanup()
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.1)

    def __enter__(self) -> Self:
        """Return the ready synthetic server for use inside a with block."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Stop the owned server without suppressing exceptions from the with block."""
        self.stop()


def isolated_core_memory_server(startup_timeout: float = 30.0) -> IsolatedCoreMemoryServer:
    """Create a ready, context-managed server with disposable synthetic-only storage."""
    return IsolatedCoreMemoryServer(startup_timeout=startup_timeout)


def _serve_child(home_arg: str, control_token: str) -> int:
    home = Path(home_arg).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if home.parent != temp_root or not home.name.startswith("lantai-core-memory-fixture-"):
        raise SystemExit("refusing to start fixture outside its disposable temp directory")
    if not home.is_dir():
        raise SystemExit("fixture temp directory must be created by its parent")

    # A second line of defense: do not trust inherited production paths or secrets.
    expected_db = home / "fixture.sqlite3"
    if Path(os.environ.get("LANTAI_HOME", "")).resolve() != home:
        raise SystemExit("LANTAI_HOME must point to the owned fixture directory")
    if os.environ.get("DATABASE_URL") != "sqlite:///" + expected_db.as_posix():
        raise SystemExit("DATABASE_URL must point to the owned fixture database")
    if (
        os.environ.get("API_KEY")
        or os.environ.get("OPENAI_API_KEY") != "fixture-only-not-a-credential"
    ):
        raise SystemExit("fixture process requires synthetic-only credential settings")

    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    from lantai.core.auth import Principal, hash_key
    from lantai.core.settings import settings
    from lantai.models.tables import ApiKey
    from lantai.services.memory_service import put_core_memory
    from lantai.storage import db as db_module

    # Prove settings resolved the child-only values before loading/initializing storage.
    if (
        Path(settings.LANTAI_HOME).resolve() != home
        or os.environ["DATABASE_URL"] != settings.DATABASE_URL
    ):
        raise SystemExit("Lantai settings escaped the fixture environment")
    if settings.LANTAI_RUN_SCHEDULER or settings.MONITOR_ENABLED:
        raise SystemExit("fixture process requires scheduler and monitor disabled by environment")

    principal = Principal(
        tenant_id=None,
        user_id=FIXTURE_USER_ID,
        agent_id=FIXTURE_AGENT_ID,
        role="user",
        allowed_lanes=["default"],
    )

    port = 0

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Seed the disposable database before announcing this loopback API as ready."""
        db_module.init_db()
        with db_module.get_session() as session:
            session.add(
                ApiKey(
                    id="fixture-api-key",
                    key_hash=hash_key(FIXTURE_TOKEN),
                    user_id=FIXTURE_USER_ID,
                    allowed_lanes=["default"],
                    is_active=True,
                )
            )
            session.commit()
        for block, content in (
            ("policy", FIXTURE_POLICY),
            ("identity", FIXTURE_IDENTITY),
            ("task", FIXTURE_TASK),
        ):
            put_core_memory(block, content, FIXTURE_NAMESPACE, principal=principal)
        print(
            json.dumps(
                {
                    "event": "ready",
                    "base_url": f"http://127.0.0.1:{port}/",
                    "port": port,
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        yield

    app = FastAPI(lifespan=lifespan)
    from lantai.api.routes_memory import router as memory_router

    app.include_router(memory_router)

    def authorize_control(request: Request) -> None:
        """Require a loopback peer and the parent-owned synthetic control token."""
        peer = request.client.host if request.client else ""
        supplied = request.headers.get("X-Fixture-Control", "")
        if peer not in {"127.0.0.1", "::1"} or not secrets.compare_digest(supplied, control_token):
            raise HTTPException(status_code=401, detail="fixture control authorization failed")

    @app.post("/__fixture__/update")
    async def update_fixture(request: Request) -> dict:
        """Validate a synthetic block update and persist it through the real service."""
        authorize_control(request)
        raw = await request.body()
        if len(raw) > MAX_FIXTURE_CONTENT_BYTES + 256:
            raise HTTPException(status_code=413, detail="fixture control payload too large")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid fixture control payload")
        if not isinstance(payload, dict) or set(payload) != {"block", "content"}:
            raise HTTPException(status_code=400, detail="invalid fixture control shape")
        block, content = payload["block"], payload["content"]
        if block not in {"policy", "identity", "task"} or not isinstance(content, str):
            raise HTTPException(status_code=400, detail="fixture control accepts only known blocks")
        if len(content.encode("utf-8")) > MAX_FIXTURE_CONTENT_BYTES:
            raise HTTPException(status_code=413, detail="fixture content too large")
        try:
            return put_core_memory(block, content, FIXTURE_NAMESPACE, principal=principal)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="synthetic fixture content rejected"
            ) from exc

    @app.post("/__fixture__/stop")
    async def stop_fixture(request: Request) -> JSONResponse:
        """Schedule loopback server shutdown after acknowledging its parent request."""
        authorize_control(request)
        threading.Timer(0.05, lambda: setattr(server, "should_exit", True)).start()
        return JSONResponse({"ok": True})

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = int(listener.getsockname()[1])
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    lifetime = threading.Timer(
        MAX_CHILD_LIFETIME_SECONDS, lambda: setattr(server, "should_exit", True)
    )
    lifetime.daemon = True
    lifetime.start()
    try:
        server.run(sockets=[listener])
    finally:
        lifetime.cancel()
        db_module.engine.dispose()
        listener.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="synthetic isolated Lantai CoreMemoryBlock HTTP fixture"
    )
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--home", required=True)
    parser.add_argument("--control-token", required=True)
    arguments = parser.parse_args()
    raise SystemExit(_serve_child(arguments.home, arguments.control_token))
