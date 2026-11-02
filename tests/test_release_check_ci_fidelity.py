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
