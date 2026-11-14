"""Ticket 83 runtime contracts for isolated evaluation launchers."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ticket83_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolated_env(monkeypatch, production_home: Path):
    monkeypatch.setenv("REMEMBRANCE_PROD_HOME", str(production_home))
    monkeypatch.setenv("API_KEY", "ticket83-test-api-key")
    monkeypatch.setenv("OPENAI_API_KEY", "ticket83-test-provider-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("EMBED_MODEL", "test-embed")


def _point_dotenv_to_empty_temp(module, tmp_path):
    root_attr = "ROOT" if hasattr(module, "ROOT") else "REPO_ROOT"
    setattr(module, root_attr, tmp_path)


def test_lme_launcher_passes_production_home_and_shared_api_key(tmp_path, monkeypatch):
    production_home = tmp_path / "production"
    _isolated_env(monkeypatch, production_home)
    module = _load_script("lme_run")

    env = module._server_env(None, None)
    headers = module._api_headers()

    assert env["API_KEY"] == headers["X-API-Key"] == "ticket83-test-api-key"


def test_microverify_launcher_passes_production_home_and_shared_api_key(tmp_path, monkeypatch):
    production_home = tmp_path / "production"
    _isolated_env(monkeypatch, production_home)
    module = _load_script("memprobe_microverify")

    env = module._server_env()
    headers = module._headers()

    assert env["API_KEY"] == headers["X-API-Key"] == "ticket83-test-api-key"


def test_memprobe_adapter_passes_production_home_and_shared_api_key(tmp_path, monkeypatch):
    production_home = tmp_path / "production"
    _isolated_env(monkeypatch, production_home)
    module = _load_script("memprobe_lantai_adapter")
    adapter = module.LantaiVerbatimAdapter(work_dir=str(tmp_path / "stores"))

    env = adapter._server_env()
    headers = adapter._headers()

    assert env["API_KEY"] == headers["X-API-Key"] == "ticket83-test-api-key"


def test_lme_double_gate_rejects_equal_home(tmp_path, monkeypatch):
    shared_home = tmp_path / "same-home"
    shared_home.mkdir()
    _isolated_env(monkeypatch, shared_home)
    module = _load_script("lme_run")
    monkeypatch.setattr(module, "EVAL_DATA_DIR", shared_home)

    with pytest.raises(SystemExit, match="双闸失败"):
        module._assert_double_gate()


def test_microverify_double_gate_rejects_equal_home(tmp_path, monkeypatch):
    module = _load_script("memprobe_microverify")
    _isolated_env(monkeypatch, module.MV_HOME)

    with pytest.raises(SystemExit, match="双闸失败"):
        module._assert_double_gate()


def test_adapter_double_gate_rejects_production_home_overlap(tmp_path, monkeypatch):
    production_home = tmp_path / "production"
    _isolated_env(monkeypatch, production_home)
    module = _load_script("memprobe_lantai_adapter")
    adapter = module.LantaiVerbatimAdapter(work_dir=str(tmp_path / "stores"))

    with pytest.raises(SystemExit, match="双闸失败"):
        adapter._assert_double_gate(production_home / "nested-evaluation")


@pytest.mark.parametrize(
    ("script", "header_name"),
    [("lme_run", "_api_headers"), ("memprobe_microverify", "_headers")],
)
def test_api_clients_fail_closed_when_api_key_is_missing(
    script, header_name, monkeypatch, tmp_path
):
    monkeypatch.delenv("API_KEY", raising=False)
    module = _load_script(script)
    _point_dotenv_to_empty_temp(module, tmp_path)

    with pytest.raises(RuntimeError, match="API_KEY"):
        getattr(module, header_name)()


def test_adapter_fails_closed_when_api_key_is_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("API_KEY", raising=False)
    module = _load_script("memprobe_lantai_adapter")
    _point_dotenv_to_empty_temp(module, tmp_path)
    adapter = module.LantaiVerbatimAdapter(work_dir=str(tmp_path / "stores"))

    with pytest.raises(RuntimeError, match="API_KEY"):
        adapter._headers()


_EXTERNAL_CWD_SCRIPTS = (
    "run_forgetting_quality.py",
    "run_autodream.py",
    "run_dry_run.py",
    "run_import.py",
    "run_wiki.py",
    "memory_overview.py",
    "run_param_matrix.py",
    "calibrate_reflection.py",
    "mark_retrieval_noise.py",
    "recall_health.py",
    "reflect_observation_status.py",
    "legacy_schema_snapshot.py",
)


@pytest.mark.parametrize("script", _EXTERNAL_CWD_SCRIPTS)
def test_script_help_imports_from_external_working_directory(tmp_path, monkeypatch, script):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["LANTAI_HOME"] = str(tmp_path / "isolated-home")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert proc.returncode == 0, (
        f"{script} failed to import from external cwd (exit={proc.returncode})"
    )
    assert proc.stdout.startswith("usage:") or proc.stdout.startswith("用法")


def test_backfill_module_import_from_external_cwd_has_no_execution_side_effects(tmp_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["LANTAI_HOME"] = str(tmp_path / "isolated-home")
    env["PYTHONIOENCODING"] = "utf-8"
    script = SCRIPTS / "lme_backfill_u2028.py"
    code = (
        "import importlib.util; "
        f"spec=importlib.util.spec_from_file_location('ticket83_backfill', {str(script)!r}); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "assert callable(module.main)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert proc.returncode == 0, (
        f"backfill import failed from external cwd (exit={proc.returncode})"
    )
