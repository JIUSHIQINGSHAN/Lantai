"""票 82：发布门禁及诊断子进程的编码契约。"""

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_health_git_sha_is_real_smoke_and_uses_explicit_utf8_decode():
    import lantai.api.routes_health as routes_health

    with pytest.MonkeyPatch.context() as monkeypatch:
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


def _load_migrate_home():
    spec = importlib.util.spec_from_file_location("ticket82_migrate_home", _MIGRATE_HOME)
    assert spec and spec.loader
    migrate_home = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate_home)
    return migrate_home


_CJK_DIAGNOSTIC = "需要管理员权限"


def _oem_native_sample(encoding: str) -> str:
    """setx 诊断在该 OEM 代码页下能出现的文字。

    中文 Windows（cp936）就是中文诊断本身；英文 OEM（cp437，GitHub windows-latest 即此）
    编不出中文，setx 也不会输出中文——改用该代码页高半区（0x80-0xFF）里的可打印字符，
    它们同样不是 ASCII、用 UTF-8 或 ANSI 解会走样，足以证明走的是 OEM 解码。
    """
    try:
        _CJK_DIAGNOSTIC.encode(encoding)
        return _CJK_DIAGNOSTIC
    except UnicodeEncodeError:
        high = bytes(range(0x80, 0x100)).decode(encoding, errors="ignore")
        native = "".join(ch for ch in high if ch.isprintable() and not ch.isspace())
        assert native, f"{encoding} 高半区没有可打印字符"
        return "Access denied: " + native[:16]


def test_setx_native_oem_stderr_decodes_cjk_diagnostic():
    migrate_home = _load_migrate_home()

    if sys.platform == "win32":
        import ctypes

        encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    else:
        encoding = "utf-8"
    sample = _oem_native_sample(encoding)
    raw_stderr = sample.encode(encoding)
    assert migrate_home._decode_setx_stderr(raw_stderr) == sample


@pytest.mark.parametrize("oem_cp", [936, 437, 850])
def test_setx_stderr_decodes_with_oem_codepage_on_any_host(monkeypatch, oem_cp):
    """在 Linux CI 上也覆盖 Windows 分支：伪造 GetOEMCP，确认按 OEM 代码页解码。

    以前这条只在真 Windows 上走 OEM 分支，且英文 OEM（cp437）下测试本身编不出中文而报错，
    Linux CI 恒绿，所以没人发现。
    """
    import types

    migrate_home = _load_migrate_home()
    fake_kernel32 = types.SimpleNamespace(GetOEMCP=lambda: oem_cp)
    monkeypatch.setattr(
        migrate_home.ctypes,
        "windll",
        types.SimpleNamespace(kernel32=fake_kernel32),
        raising=False,
    )
    monkeypatch.setattr(migrate_home, "sys", types.SimpleNamespace(platform="win32"))

    encoding = f"cp{oem_cp}"
    sample = _oem_native_sample(encoding)
    raw_stderr = sample.encode(encoding)
    assert raw_stderr != sample.encode("utf-8"), "样本必须能区分 OEM 与 UTF-8 解码"
    assert migrate_home._decode_setx_stderr(raw_stderr) == sample
