"""Exercise the release gate with real pytest, coverage and the tripwire."""

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts.release_check import suite_gate_issues

_NODE = shutil.which("node")

_NODE_CONTRACT = r"""\
import os
import re
import subprocess

import pytest


@pytest.fixture(scope="module")
def node_version():
    node = os.environ.get("LANTAI_TEST_NODE")
    if not node:
        pytest.skip("Node is unavailable in this isolated suite")
    return subprocess.run(
        [node, "--version"], capture_output=True, check=True, text=True, timeout=5
    ).stdout.strip()


def test_node_contract(node_version):
    assert re.fullmatch(r"v\d+\.\d+\.\d+", node_version), node_version
"""


@pytest.fixture
def release_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Yield an isolated mini suite whose Node capability cannot come from PATH."""
    with TemporaryDirectory(dir=tmp_path) as temp:
        root = Path(temp)
        repo = root / "repo"
        tests = repo / "tests"
        tests.mkdir(parents=True)
        (repo / "lantai.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tests / "test_ordinary.py").write_text(
            "from lantai import VALUE\n\ndef test_ordinary():\n    assert VALUE == 1\n",
            encoding="utf-8",
        )
        (tests / "test_node_contract.py").write_text(_NODE_CONTRACT, encoding="utf-8")

        empty_path = root / "empty-path"
        empty_path.mkdir()
        monkeypatch.setenv("PATH", str(empty_path))
        monkeypatch.delenv("LANTAI_TEST_NODE", raising=False)
        monkeypatch.delenv("PYTHONPATH", raising=False)
        monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
        monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        monkeypatch.setenv("PYTEST_PLUGINS", "pytest_cov.plugin")
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        assert shutil.which("node") is None
        yield repo


def _run_gate(repo: Path) -> list[str]:
    return suite_gate_issues(
        repo,
        python=str(Path(sys.executable).absolute()),
        real_home=str(repo / "watched"),
        timeout=30,
    )


def test_release_gate_blocks_missing_node(
    release_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing Node command must fail the real gate instead of skipping its contract."""
    issues = _run_gate(release_repo)
    counts = capsys.readouterr().out
    assert issues, f"The gate accepted a skipped Node contract: {counts}"
    assert "全量测试未绿" in issues[0], issues
    assert "error=1, passed=1" in counts
    assert "skipped=" not in counts


def test_release_gate_passes_with_working_node_override(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit working command must reach and pass the isolated capability check."""
    if _NODE is None:
        pytest.skip("Node is required for the positive release-gate case")
    monkeypatch.setenv("LANTAI_TEST_NODE", _NODE)

    assert _run_gate(release_repo) == []
    assert "passed=2" in capsys.readouterr().out


def test_release_gate_blocks_python_as_node_override(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANTAI_TEST_NODE", str(Path(sys.executable).absolute()))

    issues = _run_gate(release_repo)
    assert any("全量测试未绿" in issue for issue in issues), issues
    assert "failed=1, passed=1" in capsys.readouterr().out


def test_release_gate_blocks_invalid_node_override(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid explicit command must remain an error rather than using an ambient fallback."""
    monkeypatch.setenv("LANTAI_TEST_NODE", str(release_repo / "missing-node"))

    issues = _run_gate(release_repo)
    assert any("全量测试未绿" in issue for issue in issues), issues
    assert "error=1, passed=1" in capsys.readouterr().out
