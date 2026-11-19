"""Exercise Node runtime prerequisites through the real contract-test pytest CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.harness_state import env_context

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SMOKE = "tests/test_dsh_core_memory_contract.py::test_node_core_memory_suite_smoke"


def _run_contract_smoke(
    node: Path, *, setup_only: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one real pytest setup with a bounded, explicitly selected executable."""
    with (
        env_context("LANTAI_TEST_NODE", str(node)),
        env_context("PYTHONPATH", ""),
        env_context("PYTHONUTF8", "1"),
        env_context("PYTEST_ADDOPTS", ""),
    ):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                CONTRACT_SMOKE,
                "-q",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                *(["--setup-only"] if setup_only else []),
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )


def _version_only_node(tmp_path: Path, version: str) -> Path:
    """Create an owned shell executable that reports only a controlled Node version."""
    node = tmp_path / ("node.cmd" if os.name == "nt" else "node")
    if os.name == "nt":
        node.write_text(f"@echo off\necho {version}\n", encoding="utf-8")
    else:
        node.write_text(f"#!/bin/sh\nprintf '{version}\\n'\n", encoding="utf-8")
        node.chmod(0o700)
    return node


@pytest.mark.parametrize("version", ["v24.9.0", "v25.0.0"])
def test_compatible_node_version_satisfies_setup(tmp_path: Path, version: str) -> None:
    """The declared minimum and a higher major version satisfy real fixture setup."""
    node = _version_only_node(tmp_path, version)
    completed = _run_contract_smoke(node, setup_only=True)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "SETUP    M node_runtime" in output, output
    assert "1 skipped" not in output, output


def test_configured_node_below_manifest_minimum_fails_setup(tmp_path: Path) -> None:
    """Node 24.8.0 must fail fixture setup before the package smoke can execute."""
    node = _version_only_node(tmp_path, "v24.8.0")

    completed = _run_contract_smoke(node)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "ERROR at setup of test_node_core_memory_suite_smoke" in output, output
    assert "LANTAI_TEST_NODE must be Node >=24.9.0, found v24.8.0" in output, output
    assert "1 error" in output, output
    assert "1 skipped" not in output, output


def test_invalid_explicit_node_path_fails_setup(tmp_path: Path) -> None:
    """An explicitly configured nonexistent executable must error rather than skip."""
    node = tmp_path / "missing-node.exe"
    assert not node.exists()

    completed = _run_contract_smoke(node)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "ERROR at setup of test_node_core_memory_suite_smoke" in output, output
    assert "configured LANTAI_TEST_NODE could not run" in output, output
    assert "1 error" in output, output
    assert "1 skipped" not in output, output
