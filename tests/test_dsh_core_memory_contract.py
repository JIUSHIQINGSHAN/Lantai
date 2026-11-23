"""Unmocked contract from Lantai CoreMemoryBlock through the DSH adapter.

These checks use the test suite's isolated SQLite engine and the actual service,
FastAPI route, and Node 24 adapter. They intentionally do not replace ownership,
version, or snapshot-selection logic.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_CORE = ROOT / "integrations" / "dsh-core-memory" / "lib" / "core.js"
NODE_BRIDGE = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const input = JSON.parse(readFileSync(0, 'utf8'));
const core = await import(pathToFileURL(input.module).href);
try {
  let result;
  if (input.operation === 'select') {
    result = core.selectSnapshot(input.payload, input.binding, input.maxContextBytes ?? 16384);
  } else if (input.operation === 'fetch') {
    const config = core.normalizeConfig(input.config);
    const payload = await core.fetchCoreMemory(config, config.bindings[0], input.token);
    result = core.selectSnapshot(payload, config.bindings[0], config.maxContextBytes);
  } else {
    throw new Error('unknown fixture operation');
  }
  console.log(JSON.stringify({ ok: true, result }));
} catch (error) {
  console.log(JSON.stringify({ ok: false, code: error.code ?? error.name }));
}
"""


@pytest.fixture(scope="module", autouse=True)
def node_runtime() -> str:
    """Select Node >=24.9.0; reject explicit errors and skip absent ambient support."""
    configured = os.environ.get("LANTAI_TEST_NODE")
    executable = shutil.which(configured or "node") or configured
    version = ""
    if executable:
        try:
            version = _probe_node_version(executable)
        except (OSError, subprocess.SubprocessError) as exc:
            if configured:
                pytest.fail(f"configured LANTAI_TEST_NODE could not run: {exc}")
    parsed = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    supported = parsed is not None and tuple(map(int, parsed.groups())) >= (24, 9, 0)
    if configured and not supported:
        pytest.fail(f"LANTAI_TEST_NODE must be Node >=24.9.0, found {version}")
    if not os.environ.get("LANTAI_TEST_NODE") and not supported:
        pytest.skip(
            "Node 24 is unavailable; set LANTAI_TEST_NODE for DSH core-memory contract tests"
        )
    assert executable is not None
    return executable


def _probe_node_version(executable: str) -> str:
    """Read the selected executable's version once with a bounded real process."""
    return subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    ).stdout.strip()


def _node(executable: str, operation: str, **values):
    completed = subprocess.run(
        [executable, "--input-type=module", "-e", NODE_BRIDGE],
        input=json.dumps({"operation": operation, "module": str(PLUGIN_CORE), **values}),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def test_node_core_memory_suite_smoke(node_runtime: str) -> None:
    """The Python gate executes the actual Node package suite, not only a bridge snippet."""
    directory = ROOT / "integrations" / "dsh-core-memory" / "test"
    test_files = sorted(directory.glob("*.test.mjs"))
    required = {
        "core.test.mjs",
        "session.test.mjs",
        "package.test.mjs",
        "access.test.mjs",
        "ssh.test.mjs",
        "source.test.mjs",
        "control.test.mjs",
        "client.test.mjs",
    }
    assert required <= {path.name for path in test_files}, "required security tests are missing"
    completed = subprocess.run(
        [node_runtime, "--test", *(str(path) for path in test_files)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-12000:]
    assert output.strip(), "Node test runner produced no evidence"
    summary = re.search(r"(?m)^ℹ tests (\d+)\s*$", output)
    assert summary is not None, output[-12000:]
    assert int(summary.group(1)) > 0, output[-12000:]


def _real_route_client(principal):
    from lantai.api.routes_memory import router
    from lantai.core.auth import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/memory")
    app.dependency_overrides[get_current_user] = lambda: principal
    return TestClient(app)


def test_real_service_and_core_memory_route_match_node_snapshot_contract(
    node_runtime: str, param_env: tuple[Callable[[], Session], Engine]
) -> None:
    """Real SQLite writes, version bump, route JSON, then the actual Node selector."""
    from lantai.core.auth import Principal
    from lantai.services.memory_service import put_core_memory

    _session_factory, _engine = param_env
    principal = Principal(user_id="fixture-user", tenant_id=None, agent_id="fixture-agent")
    namespace = "fixture"
    put_core_memory("policy", "Fixture policy stays data.", namespace, principal=principal)
    updated = put_core_memory(
        "policy", "Updated fixture policy stays data.", namespace, principal=principal
    )
    put_core_memory("identity", "Fixture identity.", namespace, principal=principal)
    put_core_memory("task", "Fixture task is explicitly bound.", namespace, principal=principal)
    assert updated["version"] == 2, "real service must increment persisted versions"

    with _real_route_client(principal) as client:
        response = client.get("/api/memory/core-memory", params={"namespace": namespace})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict) and isinstance(payload.get("blocks"), list)
    assert {row["block"] for row in payload["blocks"]} == {"policy", "identity", "task"}
    for row in payload["blocks"]:
        assert {
            "block",
            "namespace",
            "user_id",
            "tenant_id",
            "content",
            "version",
            "updated_at",
        } <= row.keys()
        assert row["namespace"] == namespace
        assert row["user_id"] == "fixture-user"
        assert row["version"] >= 1

    binding = {
        "workspace": str(ROOT),
        "namespace": namespace,
        "userId": "fixture-user",
        "tenantId": None,
        "includeTask": True,
    }
    selected = _node(node_runtime, "select", payload=payload, binding=binding)
    assert selected["ok"] is True, selected
    assert selected["result"]["blockCount"] == 3
    assert selected["result"]["versions"] == [
        {"block": "policy", "version": 2},
        {"block": "identity", "version": 1},
        {"block": "task", "version": 1},
    ]
    assert "Updated fixture policy stays data." in selected["result"]["text"]


def test_real_route_legacy_null_owner_and_duplicate_candidate_fail_closed(
    node_runtime: str, param_env: tuple[Callable[[], Session], Engine]
) -> None:
    """A legacy NULL owner is rejected, and duplicate real candidates stay ambiguous."""
    from lantai.core.auth import Principal
    from lantai.models.tables import CoreMemoryBlock
    from lantai.services.memory_service import put_core_memory

    session_factory, _engine = param_env
    principal = Principal(user_id="fixture-user", tenant_id=None, agent_id="fixture-agent")
    namespace = "fixture"
    put_core_memory("policy", "Owned fixture policy.", namespace, principal=principal)
    with session_factory() as session:
        session.add(
            CoreMemoryBlock(
                id="legacy-null-owner-identity",
                block="identity",
                namespace=namespace,
                content="Legacy row with no recorded owner.",
                user_id=None,
                tenant_id=None,
            )
        )
        session.commit()

    with _real_route_client(principal) as client:
        response = client.get("/api/memory/core-memory", params={"namespace": namespace})
    assert response.status_code == 200, response.text
    payload = response.json()
    binding = {
        "workspace": str(ROOT),
        "namespace": namespace,
        "userId": "fixture-user",
        "tenantId": None,
        "includeTask": False,
    }
    rejected_null = _node(node_runtime, "select", payload=payload, binding=binding)
    assert rejected_null == {"ok": False, "code": "ownership_mismatch"}

    # Use an actual response row as the candidate and duplicate its complete shape;
    # the adapter must refuse ambiguity instead of choosing a newest-looking row.
    owned_policy = next(row for row in payload["blocks"] if row["block"] == "policy")
    duplicate_payload = {
        "blocks": [owned_policy, {**owned_policy, "version": owned_policy["version"] + 1}]
    }
    rejected_duplicate = _node(node_runtime, "select", payload=duplicate_payload, binding=binding)
    assert rejected_duplicate == {"ok": False, "code": "ambiguous_blocks"}


def test_isolated_real_lantai_http_fixture_flows_through_node_adapter(node_runtime: str) -> None:
    """A child process runs the real authenticated route against a disposable DB."""
    from tests.support.dsh_core_memory_server import isolated_core_memory_server

    with isolated_core_memory_server() as server:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            server.base_url + "core-memory?namespace=fixture",
            headers={"Authorization": f"Bearer {server.token}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        assert response.status == 200
        assert {row["block"] for row in payload["blocks"]} == {"policy", "identity", "task"}
        assert all(row["user_id"] == "fixture-user" for row in payload["blocks"])

        unauthenticated = urllib.request.Request(server.base_url + "core-memory?namespace=fixture")
        try:
            urllib.request.urlopen(unauthenticated, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError(
                "real Lantai route unexpectedly accepted an unauthenticated request"
            )

        changed = server.update("policy", "Policy revision from isolated control fixture.")
        assert changed["version"] == 2
        config = {
            "enabled": True,
            "baseUrl": server.base_url,
            "allowLoopbackHttp": True,
            "apiKeyEnv": "LANTAI_DSH_FIXTURE_TOKEN",
            "bindings": [
                {
                    "workspace": str(ROOT),
                    "namespace": server.namespace,
                    "userId": server.user_id,
                    "tenantId": None,
                    "includeTask": True,
                }
            ],
            "timeoutMs": 1000,
            "maxResponseBytes": 131072,
            "maxContextBytes": 16384,
        }
        selected = _node(node_runtime, "fetch", config=config, token=server.token)
        assert selected["ok"] is True, selected
        assert selected["result"]["versions"] == [
            {"block": "policy", "version": 2},
            {"block": "identity", "version": 1},
            {"block": "task", "version": 1},
        ]
        assert "Policy revision from isolated control fixture." in selected["result"]["text"]

        if os.environ.get("LANTAI_DSH_RUN_REAL_RUNTIME") == "1":
            server.update("policy", "fixture-real-policy-v1")
            powershell = shutil.which("powershell")
            if not powershell:
                pytest.fail("LANTAI_DSH_RUN_REAL_RUNTIME requires Windows PowerShell")
            runtime_env = os.environ.copy()
            runtime_env.update(
                {
                    "LANTAI_DSH_REAL_FIXTURE_URL": server.base_url,
                    "LANTAI_DSH_REAL_FIXTURE_TOKEN": server.token,
                    "LANTAI_DSH_REAL_FIXTURE_USER_ID": server.user_id,
                    "LANTAI_DSH_REAL_FIXTURE_NAMESPACE": server.namespace,
                    "LANTAI_DSH_REAL_FIXTURE_SENTINEL": "fixture-real-policy-v1",
                }
            )
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts" / "verify_dsh_core_memory.ps1"),
                    "-RuntimeOnly",
                    "-TimeoutSeconds",
                    "30",
                ],
                cwd=ROOT,
                env=runtime_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
            runtime_output = completed.stdout + completed.stderr
            assert completed.returncode == 0, runtime_output[-16000:]
            assert "realBackend=fixture" in runtime_output, runtime_output[-16000:]
            assert (
                "independent extraction switch submits only fresh main-conversation user text"
                in runtime_output
            )
            assert (
                "isolated real Lantai backend reaches the first admitted model request"
                in runtime_output
            )
            assert (
                "pending input from outside the current extraction consent never reaches Lantai"
                in runtime_output
            )
            assert re.search(r"(?m)^ℹ tests 24\s*$", runtime_output), runtime_output[-16000:]
            assert re.search(r"(?m)^ℹ pass 24\s*$", runtime_output), runtime_output[-16000:]
            assert re.search(r"(?m)^ℹ fail 0\s*$", runtime_output), runtime_output[-16000:]
