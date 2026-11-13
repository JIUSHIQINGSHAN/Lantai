"""票 82：发布门禁及诊断子进程的编码契约。"""

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_CHECK = REPO_ROOT / "scripts" / "release_check.py"
_MIGRATE_HOME = REPO_ROOT / "scripts" / "migrate_home.py"
_ROUTES_HEALTH = REPO_ROOT / "lantai" / "api" / "routes_health.py"


def _load_release_check():
    spec = importlib.util.spec_from_file_location("ticket82_release_check", _RELEASE_CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = _load_release_check()


def _run_utf8_probe(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; print('中文诊断')",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def test_real_subprocess_receives_utf8_from_isolated_probe_env(tmp_path):
    env = RC.isolated_probe_env(str(tmp_path / "probe-home"))
    assert env["PYTHONIOENCODING"].split(":", 1)[0] == "utf-8"

    proc = _run_utf8_probe(env)

    assert proc.stdout == "中文诊断\n"


def test_suite_gate_report_preserves_cjk_failure_details(tmp_path, monkeypatch):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_中文.py").write_text(
        "def test_中文():\n    assert False, '权限不足'\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")

    issues = RC.suite_gate_issues(tmp_path, timeout=180, real_home=str(tmp_path / "nohome"))

    report = "\n".join(issues)
    assert "test_中文" in report
    assert "权限不足" in report
    assert "�" not in report


def test_release_check_env_builders_pin_utf8_encoding():
    source = _RELEASE_CHECK.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_RELEASE_CHECK))
    builder_names = {
        "isolated_probe_env",
        "namespace_provenance_issues",
        "suite_gate_issues",
        "run_build_gate",
    }
    builders = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in builder_names
    }
    assert builders.keys() == builder_names
    for name, builder in builders.items():
        assignments = [
            node
            for node in ast.walk(builder)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id in {"env", "run_env"}
                and ast.unparse(target.slice).strip("\"'") == "PYTHONIOENCODING"
                for target in node.targets
            )
        ]
        assert assignments, f"{name} 未钉 PYTHONIOENCODING"
        assert all(ast.literal_eval(node.value) == "utf-8" for node in assignments)


def test_release_check_git_reads_cjk_branch_name(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "票82-编码"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    result = RC._git(repo, "branch", "--show-current")

    assert result.stdout.strip() == "票82-编码"


def test_build_gate_subprocess_passes_explicit_environment():
    source = _RELEASE_CHECK.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_RELEASE_CHECK))
    run_build_gate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_build_gate"
    )
    calls = [
        node
        for node in ast.walk(run_build_gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "run"
    ]
    assert len(calls) == 1
    env_arg = next((keyword.value for keyword in calls[0].keywords if keyword.arg == "env"), None)
    assert isinstance(env_arg, ast.Name) and env_arg.id == "env"
    env_copies = [
        node
        for node in ast.walk(run_build_gate)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "env" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "copy"
        and isinstance(node.value.func.value, ast.Attribute)
        and node.value.func.value.attr == "environ"
    ]
    assert env_copies, "build gate 应复制宿主环境以保留 uv/Python 工具链"


def test_health_git_sha_is_real_smoke_and_uses_explicit_utf8_decode(monkeypatch):
    import lantai.api.routes_health as routes_health

    monkeypatch.setattr(routes_health, "_git_sha_cache", None)
    sha = routes_health._current_git_sha()
    assert (
        sha
        == subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, encoding="utf-8"
        ).strip()
    )

    tree = ast.parse(_ROUTES_HEALTH.read_text(encoding="utf-8"), filename=str(_ROUTES_HEALTH))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "run"
    ]
    assert calls
    assert any(keyword.arg == "encoding" for keyword in calls[0].keywords)
    assert any(keyword.arg == "errors" for keyword in calls[0].keywords)


def test_setx_native_oem_stderr_decodes_cjk_diagnostic():
    spec = importlib.util.spec_from_file_location("ticket82_migrate_home", _MIGRATE_HOME)
    assert spec and spec.loader
    migrate_home = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate_home)

    raw_stderr = "需要管理员权限".encode("oem")
    assert migrate_home._decode_setx_stderr(raw_stderr) == "需要管理员权限"
