"""接入自检：真实检查流程，仅隔离子进程与 HTTP 外部 I/O。"""

import sqlite3
import subprocess
import sys

import httpx
import pytest

from scripts import verify_lantai
from tests.support import harness_state


@pytest.fixture
def diagnostic_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".chromadb").mkdir()
    with sqlite3.connect(home / "remembrance.db") as conn:
        conn.execute("CREATE TABLE diagnostic (id INTEGER)")
    monkeypatch.setattr(verify_lantai, "results", [])

    def unavailable(url, **kwargs):
        assert url == "http://127.0.0.1:8767/health"
        raise httpx.ConnectError("synthetic unavailable service")

    monkeypatch.setattr(httpx, "get", unavailable)
    with harness_state.env_context("LANTAI_HOME", str(home)):
        yield home


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, "{}"), (0, ""), (0, "   "), (0, "[1]"), (0, "null"), (0, "{bad-json")],
    ids=["failed-process", "empty", "whitespace", "array", "null", "invalid-json"],
)
def test_selfcheck_rejects_invalid_hook_output(
    diagnostic_home, monkeypatch, capsys, returncode, stdout
):
    completed = subprocess.CompletedProcess([sys.executable], returncode, stdout, "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed)
    assert verify_lantai.main() == 1
    hook = [row for row in verify_lantai.results if row[1].startswith("Shell Hook 契约")]
    assert len(hook) == 1
    assert hook[0][0] == "FAIL"
    assert hook[0][2]
    assert "Traceback" not in capsys.readouterr().out


def test_selfcheck_optional_service_is_skipped_and_does_not_fail(
    diagnostic_home, monkeypatch, capsys
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([sys.executable], 0, "{}", ""),
    )
    assert verify_lantai.main() == 0
    rest = [row for row in verify_lantai.results if row[1] == "REST /health"]
    assert len(rest) == 1 and rest[0][0] == "SKIP"
    output = capsys.readouterr().out
    assert "1 跳过" in output
    assert "0 失败" in output


def test_selfcheck_repeated_run_uses_only_current_results(diagnostic_home, monkeypatch):
    responses = iter(
        [
            subprocess.CompletedProcess([], 1, "{}", ""),
            subprocess.CompletedProcess([], 0, "{}", ""),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: next(responses))
    assert verify_lantai.main() == 1
    count = len(verify_lantai.results)
    assert verify_lantai.main() == 0
    assert len(verify_lantai.results) == count
    assert not any(status == "FAIL" for status, _, _ in verify_lantai.results)


def test_selfcheck_unhealthy_running_service_still_fails(diagnostic_home, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, "{}", "")
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(503))
    assert verify_lantai.main() == 1
    rest = [row for row in verify_lantai.results if row[1] == "REST /health"]
    assert rest == [("FAIL", "REST /health", "HTTP 503")]
