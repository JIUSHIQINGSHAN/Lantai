"""票 05：发布门禁必须与 CI 同口径（CI 步骤覆盖核对 + 遗忘质量门禁）。

病灶（实测，非推测）：`.github/workflows/tests.yml` 的测试步骤是
`pytest tests/ -q --cov=lantai --cov-report=term-missing`，而 `release_check.py`
跑的是不带 `--cov` 的命令，且**从不运行** CI 的第 4 步
`python scripts/run_forgetting_quality.py --check`。后果是 v0.66 窗口里
`tests/test_hook_timeout_budget.py` 的一条零余量墙钟断言在 CI（有插桩）判红、
在门禁（无插桩）判绿——门禁连红 9 轮而无人发现。

这些用例钉住的是「门禁漏检这一类」，不只是那一次漏检。全部离线、临时目录，
不碰宿主库、不写生产配置。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_release_check():
    spec = importlib.util.spec_from_file_location(
        "rc_fidelity", REPO / "scripts" / "release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RC = _load_release_check()


def _porcelain() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO), capture_output=True, text=True
    ).stdout


# ---------------------------------------------------------------------------
# 1. 门禁的测试口径必须真的等于 CI 口径（含覆盖率插桩）
# ---------------------------------------------------------------------------


def test_ci_workflows_do_run_pytest_with_coverage():
    """前置事实：CI 口径带 --cov。若哪天 CI 改了，本用例先提醒判据要跟着改。"""
    text = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "--cov=lantai" in text, "CI 测试步骤不再带覆盖率口径，本票的病灶前提需要重新核实"


def test_suite_pytest_command_carries_ci_coverage_flags():
    cmd = RC.suite_pytest_command()
    joined = " ".join(cmd)
    assert "--cov=lantai" in cmd, f"门禁的 pytest 口径缺少 CI 的覆盖率插桩：{joined}"
    assert "--cov-report=term-missing" in cmd, f"门禁的 pytest 口径与 CI 不一致：{joined}"
    assert "tests/" in cmd, f"门禁没跑全量目录：{joined}"


def test_suite_gate_uses_the_declared_command():
    """门禁实际执行的命令必须就是被核对的那条，不许两张皮。"""
    src = (REPO / "scripts" / "release_check.py").read_text(encoding="utf-8")
    assert re.search(r"cmd\s*=\s*suite_pytest_command\(", src), (
        "suite_gate_issues 没有改用 suite_pytest_command()，口径核对会变成纸面文章"
    )


# ---------------------------------------------------------------------------
# 2. CI 的每一步检查都必须有门禁项认领（漏一条就判红）
# ---------------------------------------------------------------------------


def test_real_ci_check_commands_are_all_owned_by_the_gate():
    issues = RC.ci_step_coverage_issues(REPO)
    assert issues == [], f"CI 有检查步骤没被发布门禁覆盖：{issues}"


def test_ci_step_owner_list_covers_the_forgetting_gate():
    assert "python scripts/run_forgetting_quality.py --check" in RC.CI_STEP_OWNERS


def test_new_unowned_ci_step_is_reported(tmp_path):
    """阴性对照：CI 新增一步而门禁没认领 → 必须判红（本票病灶的类）。"""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: python scripts/brand_new_gate.py --check\n",
        encoding="utf-8",
    )
    issues = RC.ci_step_coverage_issues(tmp_path)
    assert any("brand_new_gate" in i for i in issues), f"未认领的 CI 步骤被放行了：{issues}"


def test_stale_owner_without_ci_step_is_reported(tmp_path):
    """反向对照：门禁声称覆盖一条 CI 里已不存在的命令 → 也要判红，防清单烂账。"""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    RC.CI_STEP_OWNERS.setdefault("__probe_ghost_command__", "某门禁项")
    try:
        (wf / "x.yml").write_text(
            "jobs:\n  t:\n    steps:\n      - run: pytest tests/ -q\n", encoding="utf-8"
        )
        issues = RC.ci_step_coverage_issues(tmp_path)
        assert any("__probe_ghost_command__" in i for i in issues), issues
    finally:
        RC.CI_STEP_OWNERS.pop("__probe_ghost_command__", None)


def test_install_steps_are_not_mistaken_for_checks(tmp_path):
    """pip install / echo 不是检查项，不该要求认领（否则门禁会假红）。"""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(
        "jobs:\n  t:\n    steps:\n"
        "      - name: Install\n        run: pip install -e .[dev]\n"
        "      - run: pytest tests/ -q\n",
        encoding="utf-8",
    )
    issues = RC.ci_step_coverage_issues(tmp_path)
    assert not any("pip install" in i for i in issues), issues


def test_multi_line_run_block_yields_each_command():
    """`run: |` 多行块必须被逐条解析出来（否则 CI 里第二条命令永远核不到）。"""
    text = (
        "jobs:\n  t:\n    steps:\n      - run: |\n"
        "          ruff check lantai/ tests/ scripts/\n"
        "          ruff format --check lantai/ tests/ scripts/\n"
    )
    got = [c for body in RC.iter_run_bodies(text) for c in RC.normalize_run_commands(body)]
    assert got == [
        "ruff check lantai/ tests/ scripts/",
        "ruff format --check lantai/ tests/ scripts/",
    ]


def test_scalar_and_block_run_forms_both_parse():
    text = "steps:\n  - run: pytest tests/ -q\n  - run: >\n      python scripts/x.py --check\n"
    got = [c for body in RC.iter_run_bodies(text) for c in RC.normalize_run_commands(body)]
    assert got == ["pytest tests/ -q", "python scripts/x.py --check"]


def test_comment_lines_inside_run_block_are_ignored():
    assert RC.normalize_run_commands("# note\npytest tests/ -q") == ["pytest tests/ -q"]


# ---------------------------------------------------------------------------
# 3. 遗忘质量门禁：判据本身
# ---------------------------------------------------------------------------


def test_forgetting_gate_pass_needs_exit_zero_and_marker():
    assert RC.forgetting_gate_issues(0, "门禁结果: PASS\n") == []


def test_forgetting_gate_blocks_nonzero_exit():
    issues = RC.forgetting_gate_issues(1, "门禁结果: FAIL\n指标低于门槛\n")
    assert issues and "遗忘质量" in issues[0]


def test_forgetting_gate_blocks_zero_exit_without_evidence():
    """退出码 0 但没打印结论标记 = 空跑，不许读成通过（与全量测试同一条纪律）。"""
    issues = RC.forgetting_gate_issues(0, "")
    assert issues, issues


# ---------------------------------------------------------------------------
# 4. 真跑一次，且不留任何仓库副作用
# ---------------------------------------------------------------------------


def test_forgetting_gate_really_runs_and_pollutes_nothing():
    before = _porcelain()
    issues = RC.run_forgetting_gate(REPO)
    assert issues == [], f"遗忘质量离线门禁没通过：{issues}"
    assert _porcelain() == before, "跑遗忘质量门禁改变了工作区状态（生产污染）"


# ---------------------------------------------------------------------------
# 5. 门禁自己不许留污染（票 05 续10 实测：绊线报告每跑一次留一个文件）
# ---------------------------------------------------------------------------


def _temp_names(*prefixes):
    root = Path(tempfile.gettempdir())
    out = set()
    for pre in prefixes:
        out |= {q.name for q in root.glob(pre + "*")}
    return out


def test_suite_gate_leaves_no_temp_residue(tmp_path):
    """门禁跑完全量必须把临时产物收干净，且不把 .coverage 落进工作区。

    病灶（实测）：`suite_gate_issues` 用 `tempfile.NamedTemporaryFile` 造绊线报告路径，
    `with` 块一退出就把文件删了，随后 pytest 子进程在 `pytest_configure` 里按同名
    `open(..., "w")` 把它**重建**出来，父进程再也不管——每跑一次门禁就在 %TEMP% 里
    留一个文件（本机已积到 200 个）。门禁自己制造「文件状态残留」同样违反验收底线。
    现在报告与覆盖率数据都落在一个由门禁独占的临时目录里，退出即销毁。
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_tiny.py").write_text(
        "def test_tiny():\n    assert True\n", encoding="utf-8"
    )
    home = tmp_path / "fakehome"
    home.mkdir()

    before = _temp_names("lantai_gate_suite_", "lantai_tripwire_")
    issues = RC.suite_gate_issues(repo, timeout=900, real_home=str(home))
    after = _temp_names("lantai_gate_suite_", "lantai_tripwire_")

    assert after == before, f"门禁残留了临时产物：{sorted(after - before)}"
    assert not (repo / ".coverage").exists(), "门禁把覆盖率数据文件写进了被测仓库工作区"
    assert not any("没有真正跑起来" in i for i in issues), (
        f"报告换了位置就读不到（等于门禁失去绊线证据）：{issues}"
    )


# ---------------------------------------------------------------------------
# 6. 门禁口径必须是 CI 的**超集**，且 `-o addopts=` 这个钉子必须保持惰性
# ---------------------------------------------------------------------------


def test_gate_suite_command_is_a_superset_of_ci_pytest_steps():
    issues = RC.gate_suite_flag_issues(REPO)
    assert issues == [], f"门禁少带了 CI 会执行的项（门禁绿推不出 CI 绿）：{issues}"


def test_ci_flag_missing_from_gate_command_is_reported(tmp_path):
    """阴性对照：CI 多一项而门禁没带上 -> 必须判红。这是续10 病灶的同一类，只是方向相反：
    此前是门禁少跑一步，将来可能是门禁悄悄比 CI 宽松。"""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: pytest tests/ -q -W error::DeprecationWarning\n",
        encoding="utf-8",
    )
    issues = RC.gate_suite_flag_issues(tmp_path)
    assert any("-W" in i for i in issues), f"CI 独有项被门禁漏掉却放行：{issues}"


def test_declared_addopts_breaks_gate_ci_equivalence(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q"\n',
        encoding="utf-8",
    )
    issues = RC.addopts_fidelity_issues(tmp_path)
    assert issues and "addopts" in issues[0], f"声明了 addopts 却没报警：{issues}"


def test_addopts_pin_is_inert_today():
    """现状锁：本仓 pyproject 不声明 addopts，所以门禁里的 `-o addopts=` 是惰性的钉子
    （不是掩盖手段）。哪天真加了 addopts，这条会红——那是要求 CI 与门禁当面对账的信号，
    不许删断言、也不许靠它把 CI 的口径悄悄改掉。"""
    assert RC.addopts_fidelity_issues(REPO) == []


# ---------------------------------------------------------------------------
# 7. 构建维度：CI 的 run 命令必须**全部**对账，wheel 必须真构建真核对
# ---------------------------------------------------------------------------


def _make_wheel(dirpath, *, version, packages=("lantai/__init__.py",), with_entries=True):
    """造一个最小真 wheel（zip），不用 mock：dist-info/METADATA + 载荷文件。"""
    import zipfile

    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / f"lantai-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"lantai-{version}.dist-info/METADATA", f"Name: lantai\nVersion: {version}\n")
        if with_entries:
            zf.writestr(
                f"lantai-{version}.dist-info/entry_points.txt",
                "[console_scripts]\nlantai-mcp = lantai.cli.mcp:main\nlantai-server = lantai.api.app:main\n",
            )
        for pkg in packages:
            zf.writestr(pkg, "")
    return path


def test_unaccounted_ci_step_is_reported(tmp_path):
    """旧判据只把命中 CI_CHECK_PREFIXES 的命令当检查项（fail-open）：`python -m mypy`、
    `python -m build --wheel` 这类既不在前缀表也不在认领表，会从缝里滑走——门禁绿而 CI 红。
    现在默认全部要对账，这条负向对照锁住那个缝。"""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: python -m mypy lantai/\n",
        encoding="utf-8",
    )
    issues = RC.ci_step_coverage_issues(tmp_path)
    assert any("mypy" in i for i in issues), f"新增 CI 命令不对账也能放行：{issues}"


def test_no_real_check_can_hide_in_the_exemption_list():
    """豁免清单只能装环境准备类命令。把 pytest/ruff/scripts 塞进豁免清单 = 把检查洗成豁免。"""
    for cmd in RC.CI_STEP_EXEMPTIONS:
        assert not cmd.startswith(("pytest ", "python -m pytest ", "ruff ", "python scripts/")), (
            f"检查类命令被塞进豁免清单：{cmd}"
        )
        assert RC.CI_STEP_EXEMPTIONS[cmd].strip(), f"豁免没写理由：{cmd}"


def test_build_step_is_owned_by_a_gate_item():
    assert "python -m build --wheel" in RC.CI_STEP_OWNERS


def test_wheel_metadata_issues_reject_wrong_version_empty_package_and_no_entries(tmp_path):
    good = _make_wheel(tmp_path / "good", version="9.9.9")
    assert RC.wheel_metadata_issues(good, expected_version="9.9.9") == []

    wrong = _make_wheel(tmp_path / "wrong", version="0.0.1")
    assert any("Version" in i for i in RC.wheel_metadata_issues(wrong, expected_version="9.9.9"))

    empty = _make_wheel(tmp_path / "empty", version="9.9.9", packages=("other/__init__.py",))
    assert any("lantai" in i for i in RC.wheel_metadata_issues(empty, expected_version="9.9.9"))

    noentry = _make_wheel(tmp_path / "noentry", version="9.9.9", with_entries=False)
    assert any(
        "entry_point" in i for i in RC.wheel_metadata_issues(noentry, expected_version="9.9.9")
    )


def test_build_gate_never_passes_without_an_artifact(tmp_path):
    """构建前端跑不出 wheel 时必须判红：没有产物就没有证据，静默跳过等于没检查。"""
    issues, wheel = RC.run_build_gate(tmp_path, expected_version="9.9.9", timeout=60)
    assert wheel is None, f"空目录不该构建出东西：{wheel}"
    assert issues, "构建未取证却被读成通过"
    assert any("构建" in i for i in issues), issues


def test_run_build_gate_smoke_real_build():
    """不 mock 的冒烟：真调用一次构建。有产物 -> 版本与载荷必须对齐；
    无产物（本机没有离线构建前端）-> 必须判红。两个分支都不许静默绿。"""
    import tomllib

    version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    issues, wheel = RC.run_build_gate(REPO, expected_version=version, timeout=600)
    if wheel is None:
        assert issues, "构建前端不可用必须判红"
    else:
        assert issues == [], f"真实 wheel 未通过对账：{issues}"
        assert Path(wheel).name.endswith(".whl")
