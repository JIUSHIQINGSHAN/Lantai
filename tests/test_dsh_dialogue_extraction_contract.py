"""Real DSH extraction adapter -> authenticated Lantai route -> temporary SQLite."""

import json
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

from tests.support.dsh_core_memory_server import (
    IsolatedCoreMemoryServer,
    isolated_core_memory_server,
)
from tests.test_dsh_core_memory_contract import node_runtime as _node_runtime

node_runtime = _node_runtime

ROOT = Path(__file__).resolve().parents[1]
EXTRACTION = ROOT / "integrations" / "dsh-core-memory" / "lib" / "extraction.js"
BRIDGE = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const input = JSON.parse(readFileSync(0, 'utf8'));
const { createSessionExtractor } = await import(pathToFileURL(input.module).href);
const { normalizeConfig } = await import(new URL('./core.js', pathToFileURL(input.module)));
const config = normalizeConfig({ enabled: true, transport: 'direct', baseUrl: input.baseUrl,
  allowLoopbackHttp: true, authMode: input.authMode, apiKeyEnv: 'FIXTURE_TOKEN', timeoutMs: 3000,
  bindings: [{ workspace: input.workspace, namespace: 'fixture', userId: input.userId, tenantId: input.tenantId }] });
const extractor = createSessionExtractor({ config, binding: config.bindings[0], sessionId: 'dsh-fixture-extraction',
  getWorkspace: () => input.workspace, authorize: () => true,
  resolveCredential: async () => ({ value: input.token }) });
try {
  extractor.accept({ seq: 1, type: 'turn/start', data: { turn: 0 } });
  extractor.accept({ seq: 2, type: 'user/message', surfaceOp: 'append', data: {
    id: 'fixture-message', role: 'user', source: { kind: 'user' },
    content: [{ type: 'text', text: '记住：fixture-user 喜欢清楚简洁的中文说明。' }] } });
  await extractor.drain();
  console.log(JSON.stringify(extractor.status()));
} finally { await extractor.dispose(); }
"""


def _submit(
    executable: str,
    server: IsolatedCoreMemoryServer,
    *,
    auth_mode: str,
    user_id: str,
    tenant_id: str | None,
) -> dict:
    """Submit synthetic text with the already validated Node executable."""
    token = server.admin_token if auth_mode == "api-key" else server.token
    result = subprocess.run(
        [executable, "--input-type=module", "-e", BRIDGE],
        input=json.dumps(
            {
                "module": str(EXTRACTION),
                "baseUrl": server.base_url,
                "workspace": str(ROOT),
                "authMode": auth_mode,
                "userId": user_id,
                "tenantId": tenant_id,
                "token": token,
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=10,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _get_identity(
    server: IsolatedCoreMemoryServer, headers: dict[str, str] | None = None
) -> tuple[int, dict]:
    request = urllib.request.Request(server.base_url + "dialogue/identity", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_dialogue_identity_returns_only_verified_bearer_scope_without_memory_queries() -> None:
    """Expose two principal fields and perform no CoreMemoryBlock lookup."""
    with isolated_core_memory_server() as server:
        server.inspect_requests(reset=True)
        status, body = _get_identity(
            server,
            {
                "Authorization": "Bearer " + server.token,
                "X-Tenant-Id": "fixture-tenant",
            },
        )
        assert status == 200
        assert body == {"user_id": server.user_id, "tenant_id": "fixture-tenant"}
        observed = server.inspect_requests()
        assert observed["requests"] == [{"method": "GET", "path": "/dialogue/identity"}]
        assert "api_keys" in observed["selectTables"], (
            "the observer must see real credential lookup"
        )
        assert "corememoryblock" not in observed["selectTables"]
        assert "memoryitem" not in observed["selectTables"]
        request = urllib.request.Request(
            server.base_url + "core-memory?namespace=" + server.namespace,
            headers={"Authorization": "Bearer " + server.token},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["blocks"]
        assert "corememoryblock" in server.inspect_requests()["selectTables"], (
            "the observer must detect the real memory read used as a positive control"
        )


def test_dialogue_identity_rejects_missing_dev_and_mixed_api_key_credentials() -> None:
    """The metadata endpoint must never report DEV or environment-admin identity."""
    with isolated_core_memory_server(dev_mode=True) as server:
        server.inspect_requests(reset=True)
        status, body = _get_identity(server)
        assert status == 401
        assert "user_id" not in body and "tenant_id" not in body
        assert server.inspect_requests()["requests"] == [
            {"method": "GET", "path": "/dialogue/identity"}
        ]

    with isolated_core_memory_server() as server:
        server.inspect_requests(reset=True)
        status, body = _get_identity(
            server,
            {
                "X-API-Key": server.admin_token,
                "Authorization": "Bearer invalid-fixture-token",
            },
        )
        assert status == 401
        assert "user_id" not in body and "tenant_id" not in body
        assert server.inspect_requests()["requests"] == [
            {"method": "GET", "path": "/dialogue/identity"}
        ]


def test_identity_metadata_404_and_wrong_owner_fail_closed_before_dialogue_write(
    node_runtime: str,
) -> None:
    """Reject mismatched and unsupported identities without an ingest event."""
    with isolated_core_memory_server() as server:
        status, _body = _get_identity(server, {"Authorization": "Bearer " + server.token})
        assert status == 200
        server.inspect_requests(reset=True)
        receipt = _submit(
            node_runtime,
            server,
            auth_mode="bearer",
            user_id="wrong-owner",
            tenant_id=server.user_id,
        )
        assert receipt["state"] == "unavailable"
        assert receipt["reason"] == "ownership_mismatch"
        assert receipt["submittedCount"] == 0
        observed = server.inspect_requests()
        assert observed["requests"] == [{"method": "GET", "path": "/dialogue/identity"}]
        with closing(sqlite3.connect(server.home / "fixture.sqlite3")) as db:
            assert db.execute("SELECT count(*) FROM ingest_event").fetchone()[0] == 0

    with isolated_core_memory_server() as server:
        server.set_identity_available(False)
        receipt = _submit(
            node_runtime,
            server,
            auth_mode="bearer",
            user_id=server.user_id,
            tenant_id=server.user_id,
        )
        assert receipt["state"] == "unavailable"
        assert receipt["reason"] == "identity_unavailable"
        assert receipt["submittedCount"] == 0
        assert server.inspect_requests()["requests"] == [
            {"method": "GET", "path": "/dialogue/identity"}
        ]
        with closing(sqlite3.connect(server.home / "fixture.sqlite3")) as db:
            assert db.execute("SELECT count(*) FROM ingest_event").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("auth_mode", "user_id"), [("bearer", "fixture-user"), ("api-key", "api_key")]
)
def test_automatic_extraction_reaches_real_pipeline_once_with_attribution(
    node_runtime: str, auth_mode: str, user_id: str
) -> None:
    """Preserve authenticated ownership, attribution and one idempotent task."""
    tenant_id = "fixture-tenant"
    with isolated_core_memory_server() as server:
        receipt = _submit(
            node_runtime, server, auth_mode=auth_mode, user_id=user_id, tenant_id=tenant_id
        )
        assert receipt["state"] == "submitted", receipt
        assert receipt["submittedCount"] == 1
        task_id = receipt["taskId"]
        headers = (
            {"Authorization": "Bearer " + server.token}
            if auth_mode == "bearer"
            else {"X-API-Key": server.admin_token}
        )
        request = urllib.request.Request(
            server.base_url + "dialogue/tasks/" + task_id,
            headers={**headers, "X-Tenant-Id": tenant_id},
        )
        deadline = time.monotonic() + 5
        while True:
            with urllib.request.urlopen(request, timeout=2) as response:
                task = json.loads(response.read())
            if task["status"] in {"completed", "failed"} or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert task["status"] == "completed", task
        assert task["result"]["ingested"] is True
        assert task["result"]["fastpath"] is True
        assert task["user_id"] == user_id
        assert task["session_id"] == "dsh-fixture-extraction"
        assert task["request_session_id"] == "dsh-fixture-extraction"
        assert task["request_id"].startswith("dsh:")
        replay = _submit(
            node_runtime, server, auth_mode=auth_mode, user_id=user_id, tenant_id=tenant_id
        )
        assert replay["taskId"] == task_id, "stable event identity must replay the accepted task"
        with closing(sqlite3.connect(server.home / "fixture.sqlite3")) as db:
            row = db.execute(
                "SELECT user_id, tenant_id, session_id FROM memorycandidate WHERE id = ?",
                (task["result"]["candidate_id"],),
            ).fetchone()
            assert row == (user_id, tenant_id, "dsh-fixture-extraction")
            assert db.execute("SELECT count(*) FROM memorycandidate").fetchone()[0] == 1


@pytest.mark.parametrize("auth_mode", ["bearer", "api-key"])
def test_automatic_extraction_rejects_mismatched_authenticated_owner_before_queueing(
    node_runtime: str, auth_mode: str
) -> None:
    """Reject a configured owner mismatch before creating any ingestion event."""
    with isolated_core_memory_server() as server:
        receipt = _submit(
            node_runtime,
            server,
            auth_mode=auth_mode,
            user_id="unrelated-owner",
            tenant_id="fixture-tenant",
        )
        assert receipt["state"] == "unavailable", receipt
        assert receipt["reason"] == "ownership_mismatch"
        assert receipt["submittedCount"] == 0
        assert receipt["taskId"] == ""
        with closing(sqlite3.connect(server.home / "fixture.sqlite3")) as db:
            assert db.execute("SELECT count(*) FROM ingest_event").fetchone()[0] == 0
