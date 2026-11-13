"""发布门禁检查测试：纯函数不 mock + 临时 git 仓库真实执行。"""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_RELEASE_CHECK = Path(__file__).parent.parent / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check", _RELEASE_CHECK)
RC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RC)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ensure_real_git_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """git 可能已装但不在 PATH（便携版安装）：定位真实 git.exe 补进 PATH。

    只补环境（`monkeypatch.setenv`，用例结束自动还原，不再永久改写进程 PATH）、
    不 mock——用例仍真实执行 git 子进程（意图不变，票 05 续14 G2）；
    整机确实无 git 时显式 skip（无法真实测试），不为绿灯改断言。
    """
    if shutil.which("git"):
        return
    candidates: list[Path] = []
    override = os.environ.get("LANTAI_TEST_GIT_DIR")
    if override:
        candidates.append(Path(override))
    candidates += [
        Path(r"C:\Program Files\Git\cmd"),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd")),
        Path(r"D:\AI_Runtimes\Git\cmd"),  # 本机便携版 Git（未入 PATH）
    ]
    for d in candidates:
        if (d / "git.exe").is_file():
            monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
            return
    pytest.skip("本机无 git 可执行文件：release_check 的 git 用例无法真实执行")


@pytest.fixture
def clean_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _ensure_real_git_available(monkeypatch)
    _git(tmp_path, "init", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "file.txt").write_text("ok", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    _git(tmp_path, "remote", "add", "origin", "https://example.com/repo.git")
    return tmp_path


def test_collect_refs_real_repo_all_match_pyproject():
    refs = RC.collect_version_refs(RC.REPO_ROOT)
    labels = {label for label, _, _ in refs}
    assert {
        "pyproject.toml",
        "README 版本徽章",
        "FastAPI /docs 版本",
        "MCP serverInfo 版本",
    } <= labels
    assert any(label.startswith("README Docker 示例") for label in labels)
    pyproject_version = next(v for label, _, v in refs if label == "pyproject.toml")
    assert RC.SEMVER_RE.match(pyproject_version)
    for label, _, value in refs:
        assert value == pyproject_version, f"{label} 与 pyproject 不一致"


def test_uv_lock_self_version_is_a_checked_ref_on_real_repo():
    """票 05 续18：被提交的 `uv.lock` 自版本必须进版本一致性判据（当前是门禁盲区）。

    实测漂移：`pyproject.toml` = 0.66.0，而 `uv.lock` 里 `[[package]] name = "lantai"`
    仍是 **0.65.0**（0.66 bump 只改了 5 处 tracked 引用，没有重新 lock）。外部校验器
    `uv lock --locked --offline` 直接 exit 1：「The lockfile at `uv.lock` needs to be
    updated, but `--locked` was provided」——而门禁的版本一致性项从不读这个文件，
    于是这条漂移在 9/10 的绿灯里一路被当作 PASS 放过去。
    """
    refs = RC.collect_version_refs(RC.REPO_ROOT)
    lock_refs = [(label, value) for label, rel, value in refs if rel == "uv.lock"]
    assert lock_refs, "版本一致性判据没有覆盖 uv.lock（门禁盲区）"
    pyproject_version = next(v for label, _, v in refs if label == "pyproject.toml")
    for label, value in lock_refs:
        assert value == pyproject_version, f"{label} = {value}，期望 {pyproject_version}"


def test_stale_uv_lock_is_reported_by_consistency_check(tmp_path):
    """决定性反例：合成仓库里 lock 自版本落后一格，判据必须点名它，不许放过。"""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lantai"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "other"\nversion = "9.9.9"\n\n'
        '[[package]]\nname = "lantai"\nversion = "8.8.8"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    refs = RC.collect_version_refs(tmp_path)
    issues = RC.check_version_consistency("9.9.9", refs, None)
    assert any("uv.lock" in i and "8.8.8" in i for i in issues), issues
    # 反向保险：lock 与 pyproject 齐平时不得报这条
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "lantai"\nversion = "9.9.9"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    fixed = RC.check_version_consistency("9.9.9", RC.collect_version_refs(tmp_path), None)
    assert not any("uv.lock" in i for i in fixed), fixed


def _ledger_guard():
    """门禁账本护栏（普通生成器，可被不 mock 地直调）：进入归零，退出逐字还原。

    为什么不只是留在 fixture 里：fixture 由 pytest 托管，既不能直接调用，也就不能
    被单测「真实驱动一遍」。护栏本身必须可证伪（票 05 续20）。
    """
    before = list(RC.results)
    RC.results.clear()
    try:
        yield
    finally:
        # 用例抛错时 pytest 会把异常从 yield 点扔进来：不套 finally 的话这一行不执行，
        # 残渣就会串给同文件后续用例（续20 实测：异常路径留下的判定会连带第二条假失败）。
        RC.results[:] = before


@pytest.fixture(autouse=True)
def _isolate_gate_ledger():
    """用例进入时账本归零、退出时逐字还原（票 05 续19 D2 的测试侧护栏）。"""
    yield from _ledger_guard()


def _stub_gate_producers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    issues: bool = False,
    stub_lint: bool = True,
) -> None:
    """把 10 项判据的**生产者**换成固定回报，只让「记账 + 汇总 + 打印」这段真跑。

    续19 的被测对象是门禁自身的记账与诊断输出，不是各项探针——探针已在各自用例里
    真实执行过（lint / 构建 / schema 漂移 / 绊线 / 遗忘质量）。这里不 stub
    `check()`/`summarize()`/`main()`：那三个正是被测物。
    `stub_lint=False` 留给 D1 用例，让真实 lint 在「找不到 ruff」下走一遍。
    """
    verdict = ["示例问题（测试构造）"] if issues else []
    if stub_lint:
        monkeypatch.setattr(RC, "lint_gate_issues", lambda root, skips=None: list(verdict))
    monkeypatch.setattr(RC, "ci_step_coverage_issues", lambda root: list(verdict))
    monkeypatch.setattr(RC, "gate_suite_flag_issues", lambda root: list(verdict))
    monkeypatch.setattr(RC, "addopts_fidelity_issues", lambda root: list(verdict))
    monkeypatch.setattr(RC, "docs_reference_issues", lambda root: list(verdict))
    monkeypatch.setattr(
        RC, "collect_version_refs", lambda root: [("pyproject.toml", "pyproject.toml", "0.66.0")]
    )
    monkeypatch.setattr(RC, "check_version_consistency", lambda v, refs, log: list(verdict))
    monkeypatch.setattr(
        RC,
        "run_build_gate",
        lambda root, expected_version: (list(verdict), "demo-0.0.1-py3-none-any.whl"),
    )
    monkeypatch.setattr(
        RC,
        "git_issues",
        lambda root, version, allow_dirty, online=False, skips=None: list(verdict),
    )
    monkeypatch.setattr(RC, "mcp_exposure_issues", lambda root: list(verdict))
    monkeypatch.setattr(RC, "schema_drift_issues", lambda root: list(verdict))
    monkeypatch.setattr(RC, "retention_symmetry_issues", lambda root: list(verdict))
    monkeypatch.setattr(
        RC, "legacy_parity_probe", lambda root, **kw: {"anchors": [{"source_tag": "v0.0.1"}]}
    )
    monkeypatch.setattr(RC, "legacy_parity_issues_from_payload", lambda payload: list(verdict))
    monkeypatch.setattr(RC, "suite_gate_issues", lambda root, timeout=None: list(verdict))
    monkeypatch.setattr(RC, "namespace_provenance_issues", lambda root, **kw: list(verdict))
    monkeypatch.setattr(RC, "run_forgetting_gate", lambda root: list(verdict))


def _last_summary(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.startswith("结果:")]
    assert lines, f"门禁没打印汇总行：{text!r}"
    return lines[-1]


def test_gate_ledger_is_not_inherited_across_runs(monkeypatch, capsys):
    """续19 D2：门禁账本必须「每次运行一本」，同进程连跑结果逐字可复现。

    修复前实测（.scratch/gate/r19/repro_d2_accumulate.py）：模块级 `results`
    从不清空，同进程第二次 `main()` 报「**16/19 通过, 2 失败**」——上一轮的判定
    被重复计数，读者看到的分母、通过率、失败条数全是编的。
    """
    _stub_gate_producers(monkeypatch)
    codes: list[int] = []
    summaries: list[str] = []
    for _ in range(2):
        codes.append(RC.main(["v0.66.0"]))
        summaries.append(_last_summary(capsys.readouterr().out))
    assert codes == [0, 0], codes
    assert summaries[0] == summaries[1], f"两次运行汇总不同 = 账本跨运行累积：{summaries}"
    # 票 04 起 11 项（新增「命名空间 provenance」）；票 29 起 13 项
    # （新增「观测面保留对称性」）。新增检查项时同步这里的分母。
    assert "13/13 通过" in summaries[0], summaries[0]
    assert len(RC.results) == 13, f"账本残留上一轮判定：{RC.results}"


def test_lint_gate_missing_ruff_registers_skip(monkeypatch, tmp_path):
    """续19 D1：找不到 ruff 时不得回报「无问题」——必须登记为未取证。

    修复前实测（.scratch/gate/r19/repro_d1_failopen.py）：`_find_ruff` 返回空串时
    `lint_gate_issues()` 打印一行 [SKIP] 就 `return []`，而 skipped 账上根本没有
    这一项。缺的是开发期工具，不该判红，但**绝不能记成通过**。
    """
    monkeypatch.setattr(RC, "_find_ruff", lambda root: "")
    skips: list[str] = []
    issues = RC.lint_gate_issues(tmp_path, skips=skips)
    assert issues == [], issues
    assert skips == ["CI lint 门禁"], skips


def test_main_never_claims_pass_for_lint_it_never_ran(monkeypatch, capsys):
    """续19 D1 消费侧：lint 未执行时 main() 不得打 [PASS]，且必须以未取证阻断。"""
    _stub_gate_producers(monkeypatch, stub_lint=False)
    monkeypatch.setattr(RC, "_find_ruff", lambda root: "")
    code = RC.main(["v0.66.0"])
    out = capsys.readouterr().out
    assert "[PASS] CI lint 门禁" not in out, out
    assert "[SKIP] CI lint 门禁" in out, out
    assert "1 跳过" in _last_summary(out), out
    assert code == 1, f"有检查项未取证却 exit 0：{code}"


def test_failure_line_carries_category_and_minimal_repro(capsys):
    """续19 D3：失败输出必须含失败阶段、根因分类、最小复现（验收线点名要求）。"""
    RC.check("版本一致性", False, "示例：某处版本引用不等")
    out = capsys.readouterr().out
    assert "失败阶段=版本一致性" in out, out
    assert "根因分类=发布元数据漂移" in out, out
    assert "最小复现=" in out, out


def test_unknown_item_failure_is_not_silently_undiagnosed(capsys):
    """反向保险：诊断清单缺项时也要显式标「未分类」，不许静默少一段诊断。"""
    RC.check("全新门禁项", False, "x")
    out = capsys.readouterr().out
    assert "未分类" in out, out


def test_every_gate_item_exercised_by_main_has_a_diagnosis_entry(monkeypatch, capsys):
    """结构性锁：main() 实际判定的每一项都必须有根因分类 + 最小复现。"""
    _stub_gate_producers(monkeypatch, issues=True)
    RC.main(["v0.66.0"])
    out = capsys.readouterr().out
    names = {m.group(1) for m in re.finditer(r"^\[FAIL\] (.+?) —", out, re.M)}
    assert len(names) >= 10, f"main() 只判定出 {len(names)} 项：{sorted(names)}"
    missing = sorted(names - set(RC.FAILURE_DIAGNOSIS))
    assert not missing, f"这些门禁项缺根因分类/最小复现：{missing}"
    assert not [ln for ln in out.splitlines() if "未分类" in ln], out


def test_diagnosis_entries_use_declared_vocabulary():
    """诊断清单不得为空、不得跑野口径：分类必须落在申报词表内。"""
    assert RC.FAILURE_DIAGNOSIS, "门禁根因诊断清单为空"
    assert all(len(v) == 2 and v[0] and v[1] for v in RC.FAILURE_DIAGNOSIS.values())
    bad = {k: v for k, v in RC.FAILURE_DIAGNOSIS.items() if v[0] not in RC.DIAGNOSIS_CATEGORIES}
    assert not bad, bad
    assert {"测试污染", "约束漂移", "依赖缺失", "逻辑缺陷", "契约/策略违反"} <= set(
        RC.DIAGNOSIS_CATEGORIES
    )


def test_addopts_check_fail_closed_when_pin_is_gone(monkeypatch, tmp_path):
    """续19 D4（与 D1 同族）：钉子被摘掉时，一致性核对不许自己关掉还报无问题。

    修复前实测：`addopts_fidelity_issues()` 第一句就是
    `if "addopts=" not in suite_pytest_command(): return []`——谁把门禁命令里的
    `-o addopts=` 删掉，这项检查立刻变成恒空的摆设，且以 `[]` 的形式被 main() 记成
    `[PASS] CI 步骤覆盖核对`。同类病灶第 N 次复现：没取证被当成取到证。
    """
    monkeypatch.setattr(
        RC, "suite_pytest_command", lambda **kw: ["python", "-m", "pytest", "tests/"]
    )
    issues = RC.addopts_fidelity_issues(tmp_path)
    assert issues and "addopts" in issues[0], issues


def test_addopts_check_fail_closed_when_pyproject_is_missing(monkeypatch, tmp_path):
    """续19 D4：读不到 pyproject 也要判「未取证」，不许 `return []` 蒙过去。"""
    monkeypatch.setattr(
        RC,
        "suite_pytest_command",
        lambda **kw: ["python", "-m", "pytest", "tests/", "-o", "addopts="],
    )
    issues = RC.addopts_fidelity_issues(tmp_path)  # tmp_path 下没有 pyproject.toml
    assert issues and "pyproject" in issues[0], issues


def test_addopts_check_still_passes_on_real_repo():
    """反向保险：收紧不许误伤现况——钉子在场 + pyproject 未声明 addopts = 惰性钉子。"""
    assert RC.addopts_fidelity_issues(RC.REPO_ROOT) == [], "真实仓库现况被误判"


def test_gate_ledger_writer_really_leaves_residue_in_the_module_global():
    """污染者：证明门禁账本**确实**是用例间共享的模块全局（护栏不是空转）。

    沿用票 01 的「污染者 → 受害者」配对写法：同文件内按定义顺序执行，配对顺序本身
    就是断言的一部分——护栏缺失时下一条必红。
    """
    RC.check("用例内留下的残渣", False, "x")
    assert [name for _, name, _ in RC.results] == ["用例内留下的残渣"], RC.results


def test_gate_ledger_is_pristine_for_the_next_case():
    """受害者：进入本用例时账本必须为空——上一例的残渣不许跨用例活着。

    修复前（无 `_isolate_gate_ledger` 护栏）实测：污染者的判定会留在模块全局里，
    既串到后续用例，也让 `main()` 的账本起点不再确定（D2 的同族形态）。
    """
    assert RC.results == [], f"上一个用例把门禁账本带进了本用例：{RC.results}"


def test_gate_ledger_guard_clears_on_entry_and_restores_on_exit():
    """腿一：正常路径——进入时归零，退出时把进入前的状态**逐字**还原（不是清成空）。"""
    sentinel = [(RC.PASS, "用例开始前就存在的判定", "")]
    RC.results[:] = sentinel
    gen = _ledger_guard()
    next(gen)
    assert RC.results == [], "护栏进入时没有归零门禁账本（clear 腿缺失 = D2 同族）"
    RC.check("用例内的污染", False, "x")
    assert len(RC.results) == 1, RC.results
    with pytest.raises(StopIteration):
        next(gen)
    assert RC.results == sentinel, f"护栏没把进入前的账本逐字还原：{RC.results}"
    RC.results.clear()


def test_gate_ledger_guard_restores_even_when_the_case_raises():
    """腿二：异常路径——用例抛错时护栏仍必须还原，否则残渣串给后续用例造第二个假失败。

    裸 `yield` 的写法在用例失败时由 pytest `gen.throw()` 进入，异常直接从 yield 点
    穿出去，后半段还原语句根本不执行。这不是理论问题：门禁用例里任何一条断言判红，
    同文件后面的账本类用例都会因为读到上一轮的残渣而连带判红，根因被噪声盖住。
    """
    sentinel = [(RC.PASS, "用例开始前就存在的判定", "")]
    RC.results[:] = sentinel
    gen = _ledger_guard()
    next(gen)
    assert RC.results == []
    RC.check("用例内炸之前的污染", False, "x")
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("用例内炸了"))
    assert RC.results == sentinel, f"异常路径没还原账本，残渣会串给下一条：{RC.results}"
    RC.results.clear()


def test_changelog_snapshot_parses_latest_release():
    text = "## [Unreleased]\n\n## [0.3.7] - 2026-08-04\n"
    assert RC.changelog_snapshot(text) == (True, "0.3.7")
    assert RC.changelog_snapshot("## [0.3.7]\n") == (False, "0.3.7")
    assert RC.changelog_snapshot("## [Unreleased]\n") == (True, None)


def test_consistency_reports_mismatch_and_changelog_drift():
    refs = [
        ("pyproject.toml", "pyproject.toml", "0.3.7"),
        ("FastAPI /docs 版本", "api_server.py", "0.3.0"),
    ]
    issues = RC.check_version_consistency("0.3.8", refs, "## [Unreleased]\n\n## [0.3.7]\n")
    assert any("api_server.py" in i and "0.3.0" in i for i in issues)
    assert any("CHANGELOG 最新发布段" in i for i in issues)


# ---- 票 05 续46：CHANGELOG 缺失被静默豁免 = 门禁脚本假绿 ----------------------
# 病灶（修复前真实字节直调实测，见 .scratch/gate/baseline/r62-red-before-fix.log）：
# main() :1269 用 `changelog_path.exists() else None` 取文本，而判据 :190 写的是
# `if changelog_text is not None:`——CHANGELOG.md 被删/改名/路径打错时，两个
# CHANGELOG 子判据整体不执行，版本一致性照样 [PASS]（红样输出逐字：
# `[PASS] 版本一致性 — 2 处版本引用 + CHANGELOG 最新段 = v0.66.0`，而文件不存在）。
# CHANGELOG.md 不在 VERSION_REF_SPECS（7 处=pyproject/README×2/app.py/mcp.py/uv.lock），
# refs 侧兜不住。与同脚本法典化口径矛盾：addopts「依据缺失 = 未取证，不放行」(:1038)、
# 遗忘门禁「没回报结论标记 ≠ 取到证」(:1061)、MCP 探针「0 个 = fail-closed」(:630)。


def test_missing_changelog_text_blocks_not_waived():
    """判据级（零 mock）：changelog_text=None 必须产出阻断项，不得读成「无需核对」。"""
    refs = [
        ("pyproject.toml", "pyproject.toml", "0.66.0"),
        ("uv.lock 根包版本", "uv.lock", "0.66.0"),
    ]
    issues = RC.check_version_consistency("0.66.0", refs, None)
    assert issues, "CHANGELOG 文本缺失被豁免——版本一致性假绿（依据缺失=未取证）"
    assert any("CHANGELOG" in i for i in issues), issues


_ALIGNED_REFS = [
    ("pyproject.toml", "pyproject.toml", "0.66.0"),
    ("uv.lock 根包版本", "uv.lock", "0.66.0"),
]


def _stub_other_gate_producers(monkeypatch: pytest.MonkeyPatch) -> None:
    """把版本一致性以外全部检查项的生产者钉成「已取证且通过」，check_version_consistency
    与取文本/记账/汇总路径**保持真实**——比 _stub_gate_producers 更严：被追责的判据
    不许被 stub（stub 它就是把病灶豁免写进测试）。新增检查项时同步补桩。"""
    monkeypatch.setattr(RC, "lint_gate_issues", lambda root, skips=None: [])
    monkeypatch.setattr(RC, "ci_step_coverage_issues", lambda root: [])
    monkeypatch.setattr(RC, "gate_suite_flag_issues", lambda root: [])
    monkeypatch.setattr(RC, "addopts_fidelity_issues", lambda root: [])
    monkeypatch.setattr(RC, "collect_version_refs", lambda root: list(_ALIGNED_REFS))
    monkeypatch.setattr(RC, "run_build_gate", lambda root, expected_version: ([], "demo.whl"))
    monkeypatch.setattr(RC, "git_issues", lambda root, v, ad, online=False, skips=None: [])
    monkeypatch.setattr(RC, "mcp_exposure_issues", lambda root: [])
    monkeypatch.setattr(RC, "schema_drift_issues", lambda root: [])
    monkeypatch.setattr(RC, "retention_symmetry_issues", lambda root: [])
    monkeypatch.setattr(
        RC, "legacy_parity_probe", lambda root, **kw: {"anchors": [{"source_tag": "v0.0.1"}]}
    )
    monkeypatch.setattr(RC, "legacy_parity_issues_from_payload", lambda payload: [])
    monkeypatch.setattr(RC, "suite_gate_issues", lambda root, timeout=None: [])
    monkeypatch.setattr(RC, "namespace_provenance_issues", lambda root, **kw: [])
    monkeypatch.setattr(RC, "run_forgetting_gate", lambda root: [])


def test_main_fails_version_check_when_changelog_absent(monkeypatch, capsys, tmp_path):
    """消费级单变量实验：其余版本引用全齐平、只缺 CHANGELOG.md——修复前这里
    [PASS] + exit 0（假绿），修复后必须 [FAIL] 版本一致性 + exit 1。"""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lantai"\nversion = "0.66.0"\n', encoding="utf-8"
    )
    assert not (tmp_path / "CHANGELOG.md").exists()
    monkeypatch.setattr(RC, "REPO_ROOT", tmp_path)
    _stub_other_gate_producers(monkeypatch)

    code = RC.main(["v0.66.0"])
    out = capsys.readouterr().out
    assert "[PASS] 版本一致性" not in out, (
        f"CHANGELOG 不存在仍打 [PASS]（依据缺失被读成通过）：\n{out[-600:]}"
    )
    assert any(ln.startswith("[FAIL] 版本一致性") for ln in out.splitlines()), out[-600:]
    assert code == 1, f"该维度未取证却整体 exit 0：{code}"


def test_main_passes_version_check_when_changelog_present(monkeypatch, capsys, tmp_path):
    """反向保险（防判据修成恒红摆设）：CHANGELOG 齐备且与目标版本对齐时，
    版本一致性必须仍 [PASS]、整体 exit 0。"""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lantai"\nversion = "0.66.0"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.66.0]\n- 本次发布\n", encoding="utf-8"
    )
    monkeypatch.setattr(RC, "REPO_ROOT", tmp_path)
    _stub_other_gate_producers(monkeypatch)

    code = RC.main(["v0.66.0"])
    out = capsys.readouterr().out
    assert "[PASS] 版本一致性" in out, out[-600:]
    assert code == 0, f"依据齐全时不得误杀：{code}\n{out[-400:]}"


def test_normalize_version_accepts_v_prefix():
    assert RC.normalize_version("v0.3.8") == "0.3.8"
    assert RC.normalize_version("0.3.8") == "0.3.8"


def test_git_clean_master_untagged_passes(clean_repo):
    assert RC.git_issues(clean_repo, "0.3.8", allow_dirty=False) == []


def test_git_dirty_and_existing_tag_reported(clean_repo):
    (clean_repo / "dirty.txt").write_text("x", encoding="utf-8")
    _git(clean_repo, "tag", "v0.3.8")
    issues = RC.git_issues(clean_repo, "0.3.8", allow_dirty=False)
    assert any("工作区不干净" in i for i in issues)
    assert any("tag v0.3.8 已存在" in i for i in issues)


def test_git_non_master_branch_reported(clean_repo):
    _git(clean_repo, "checkout", "-b", "feature")
    issues = RC.git_issues(clean_repo, "0.3.8", allow_dirty=False)
    assert any("发布分支应为 master" in i for i in issues)


def test_git_online_reports_remote_tag(clean_repo, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(clean_repo, "remote", "set-url", "origin", str(remote))
    _git(clean_repo, "push", "origin", "master")
    _git(clean_repo, "tag", "v0.3.8")
    _git(clean_repo, "push", "origin", "v0.3.8")
    issues = RC.git_issues(clean_repo, "0.3.8", allow_dirty=False, online=True)
    assert any("远程 origin 已存在 tag v0.3.8" in i for i in issues)


# ── CI lint 门禁自查（票 21）──────────────────────────────────
# 真实跑 ruff，不 mock——「mock 掉 ruff 让测试绿」等于什么都没测。


def test_lint_gate_passes_on_real_repo():
    """本仓当前状态必须双绿（否则这条红，别先改测试）。

    这条同时是**回归哨兵**：任何人提交了没 `ruff format` 过的代码，
    这里立刻红，不用等 CI 才发现 lint 拦着全量测试不让跑。
    """
    issues = RC.lint_gate_issues(RC.REPO_ROOT)
    assert issues == [], "CI lint 门禁未通过：\n" + "\n".join(issues)


def test_lint_gate_catches_unformatted_file(tmp_path):
    """决定性：lint 自查**真的能抓到**格式违规（不是恒空的摆设）。

    造一个必然不合规的文件（ruff format 会重排的写法），跑真 ruff，
    断言 issues 非空且点名了 `ruff format`。
    """
    (tmp_path / "lantai").mkdir()
    (tmp_path / "lantai" / "bad.py").write_text("x = { 'a':1,\n  'b':2 }\n", encoding="utf-8")
    issues = RC.lint_gate_issues(tmp_path)
    assert issues, "格式违规没被抓到——lint 自查是恒空的摆设"
    assert any("ruff format" in i for i in issues), f"没点名 format 步骤：{issues}"


def test_lint_gate_catches_lint_violation(tmp_path):
    """`ruff check` 维度也要能抓到（未使用的导入）。"""
    (tmp_path / "lantai").mkdir()
    (tmp_path / "lantai" / "bad.py").write_text("import os\nx = 1\n", encoding="utf-8")
    issues = RC.lint_gate_issues(tmp_path)
    assert any("ruff check" in i for i in issues), f"没抓到 lint 违规：{issues}"


# ── 票 05（v0.66 发布门禁）：门禁必须真能阻断四类问题 ──────────────────
# 每条都配「决定性」反例：检查项必须是**能抓到东西**的，恒真/恒空的摆设等于没门禁。


def test_suite_gate_blocks_on_red_suite(tmp_path):
    """决定性：红主干必须拦下——造一个必然失败的 pytest 项目，真跑子进程。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_red.py").write_text(
        "def test_red():\n    assert False\n", encoding="utf-8"
    )
    issues = RC.suite_gate_issues(tmp_path, timeout=180, real_home=str(tmp_path / "nohome"))
    assert issues, "全量测试红着，发布门禁却没意见——门禁不跑测试等于没门禁"
    assert any("全量测试" in i for i in issues), f"没点名测试失败：{issues}"


def test_suite_gate_passes_on_green_suite(tmp_path):
    """对照：绿项目必须放行（否则这条检查是恒真的摆设）。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_green.py").write_text(
        "def test_green():\n    assert True\n", encoding="utf-8"
    )
    assert RC.suite_gate_issues(tmp_path, timeout=180, real_home=str(tmp_path / "nohome")) == []


def test_suite_gate_catches_real_home_pollution(tmp_path):
    """决定性：测试连到宿主真实 LANTAI_HOME 库，绊线必须抓到并拦下。"""
    home = tmp_path / "host_home"
    home.mkdir()
    dbp = home / "remembrance.db"
    dbp.write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    db_repr = repr(str(dbp))
    src = (
        "import sqlite3\n"
        "\n"
        "def test_open_host_db():\n"
        f"    con = sqlite3.connect({db_repr})\n"
        "    con.close()\n"
    )
    (tmp_path / "tests" / "test_pollute.py").write_text(src, encoding="utf-8")
    issues = RC.suite_gate_issues(tmp_path, timeout=180, real_home=str(home))
    assert any("真实数据目录" in i for i in issues), f"宿主库被连接却没拦下：{issues}"


def test_real_home_hits_parser_only_counts_hit_lines():
    assert RC.real_home_hits("[HIT] test=a connect(b)\n[miss] test=c connect(d)\n") == [
        "[HIT] test=a connect(b)"
    ]
    assert RC.real_home_hits("[miss] x\n") == []


def test_mcp_exposure_issue_is_structural_not_threshold():
    """判据是结构性的（默认集是否等于全量注册表），不是可被调大的阈值。"""
    assert RC.mcp_exposure_issue(96, 96) is not None
    assert RC.mcp_exposure_issue(5, 96) is None
    assert RC.mcp_exposure_issue(0, 0) is None, "空注册表不该谎报为阻断"


def test_mcp_exposure_issues_on_real_repo_explains_itself():
    issues = RC.mcp_exposure_issues(RC.REPO_ROOT)
    assert issues == [], f"真实仓库的默认档必须经门禁探针实测收窄（票 05 续31），实得：{issues}"
    # 兜住「探针自己坏了」的形态：若该列表将来非空（回归或被拦），每一项都得点名病灶。
    for issue in issues:
        assert "未收窄" in issue, f"阻断项必须点名病灶与口径：{issues}"


def _advertised_widening(msg: str) -> str | None:
    """从门禁提示里解析「唯一放宽到全量的取值」（固定句式，便于与生产实测对账）。"""
    hit = re.search(r"只有显式声明 ([A-Za-z_]+) 才允许全量", msg)
    return hit.group(1) if hit else None


def test_mcp_fail_message_only_advertises_remedies_that_really_narrow(monkeypatch):
    """门禁提示点名的「唯一放宽取值」必须与生产实测双向对账（票 05 续21 立约，续31 换绑新语义）。

    续21 时的病灶：默认档 `return TOOLS`，提示却可能开出「设 safe/none 就收窄」的
    假处方。续31 把生产语义改成 fail-closed 白名单后，不变量换了一头——现在要钉的是
    「除显式 `full` 之外没有任何取值能拿到全量」。本用例全程真调
    `lantai/cli.mcp::_get_active_tools()`（monkeypatch 只是档位入口，不 mock 计算），
    做双向对账：提示点名的放宽取值实测必须真放宽；实测能放宽的取值必须只是它的
    大小写/空格变体（混进第二条放宽路径 = 没被说破的后门，判红）。抽掉告警语、
    把处方换成 safe、生产加一条 `all` 后门、默认档被改回全量——四个方向各咬一次。
    """
    from lantai.cli import mcp

    total = len(mcp.TOOLS)
    core = set(mcp.HERMES_CORE_TOOLS)
    registry = set(mcp.TOOLS)
    SAMPLED = (
        "hermes",
        "core",
        "slim",
        "minimal",
        "safe",
        "none",
        "typo-value",
        "all",  # 评审线 N-04 的 LANTAI_MCP_EXPOSE 档位名：绝不容许成为第二条放宽后门
        "full",
        "FULL",
        " full ",
    )

    def active_for(value: str) -> set:
        monkeypatch.setenv("LANTAI_MCP_PROFILE", value)
        try:
            return set(mcp._get_active_tools())
        finally:
            monkeypatch.delenv("LANTAI_MCP_PROFILE", raising=False)

    wideners = [v for v in SAMPLED if active_for(v) == registry]
    assert wideners and all(v.strip().lower() == "full" for v in wideners), (
        f"能拿到全量注册表的取值不止显式 full：{wideners}——多出来的是没被说破的放宽后门"
    )
    for value in SAMPLED:
        if value not in ("full", "FULL", " full "):
            assert active_for(value) == core, f"非 full 取值 {value!r} 没有落到核心集"
    monkeypatch.delenv("LANTAI_MCP_PROFILE", raising=False)
    assert set(mcp._get_active_tools()) == core, (
        "不设 profile 的默认档不再是核心集——续31 的收窄被改回去了"
    )

    msg = RC.mcp_exposure_issue(total, total)
    assert msg is not None, "默认全量暴露必须判红——本用例不是来放宽它的"

    advertised = _advertised_widening(msg)
    assert advertised, "提示必须显式点名唯一放宽取值（固定句式：只有显式声明 X 才允许全量…）"
    assert advertised == "full", f"提示点名的放宽取值漂移：{advertised!r}（生产认的是 full）"

    assert "未知取值" in msg and "告警" in msg, f"提示必须说破未知取值 fail-closed 且告警：{msg}"
    assert "默认档" in msg and "不会让它变绿" in msg, f"提示必须说破本项只看默认档：{msg}"
    assert "LANTAI_MCP_EXPOSE" in msg and "不存在" in msg, f"提示必须点名缺失的开关：{msg}"
    assert "阻断发布" in msg and "可以放行" not in msg, "口径仍是阻断，不许出现放行措辞"


def test_isolated_probe_env_isolates_home_and_forbids_dev_mode(tmp_path):
    home = str(tmp_path / "probe_home")
    env = RC.isolated_probe_env(home)
    assert os.path.normcase(env["LANTAI_HOME"]) == os.path.normcase(home)
    assert env.get("API_KEY"), "探针需要非空 API_KEY（空则 DEV MODE 回退掩盖真实分支）"
    assert "LANTAI_MCP_PROFILE" not in env, "探针必须模拟『不设 profile』的默认暴露现场"


def test_isolated_probe_env_neutralizes_ambient_legacy_home(tmp_path, monkeypatch):
    """回归锁（票 05 续25）：探针 env 必须把旧名 REMEMBRANCE_HOME 也指进沙箱。

    实测病灶：`release_check.py` 的 `isolated_probe_env()` 写的是
    `REMEMENTRANCE_HOME`（多一个 E，settings 里根本没有这个字段），注释却声称
    "旧变量名兼容（settings 仍会读它）"——防线只有注释没有实现。本机装机史里
    REMEMBRANCE_HOME 是真实写过的用户级变量（docs/hermes-install-handoff.md:55
    「写用户级环境变量 REMEMBRANCE_HOME」），ambient 宿主值会原样穿透进探针 env；
    settings 靠 LANTAI_HOME 优先才没走偏——那是运气不是设计。同仓
    `scripts/legacy_schema_snapshot.py:112` 用的就是正确键名，两条隔离路径口径分叉。

    判据：塞一个敌意 ambient 旧名目录，探针 env 必须已被改写为沙箱；拼错的死键
    不许留在 env 里（留着也只是噪声，settings 读不到）。
    """
    home = str(tmp_path / "probe_home")
    monkeypatch.setenv("REMEMBRANCE_HOME", str(tmp_path / "HOST-legacy-home"))
    env = RC.isolated_probe_env(home)
    assert os.path.normcase(env.get("REMEMBRANCE_HOME", "")) == os.path.normcase(home), (
        "旧名 REMEMBRANCE_HOME 未被沙箱化：探针继承宿主旧名目录——注释声称的兼容兜底"
        "因键名拼错（REMEMENTRANCE）而虚设（票 05 续25）"
    )
    assert "REMEMENTRANCE_HOME" not in env, "拼错的死键名仍挂在探针 env 上"


def test_isolated_probe_env_blackholes_llm_egress(tmp_path, monkeypatch):
    """回归锁（票 05 续27 环境泄漏）：探针 env 必须把 LLM/精排出网端点钉到环回黑洞，

    且不得让 ambient 的真实端点/凭据穿透进探针子进程。

    实测病灶：`isolated_probe_env()` 只把 API_KEY / OPENAI_API_KEY 两把 key 中和成
    探针假 key，却原样 `os.environ.copy()` 携带了 ambient（以及 settings 经 env_file
    加载的仓根 .env）的 `OPENAI_BASE_URL` / `RERANKER_BASE_URL`，且从不处理
    `RERANKER_API_KEY`。于是探针子进程可能拿着假 key 指向**真实白名单端点**：一旦某条
    代码路径真的发起调用，请求会在拿到 401 之前就已出网——这与 harness_state 的
    「失败即断网，不是失败即外传」黑洞纪律（`127.0.0.1:9` discard）正好相反。同仓
    测试侧早已把 `OPENAI_BASE_URL` 钉成黑洞，唯独门禁探针这条隔离路径漏了这一维。

    判据（不依赖被测常量，独立口径）：出网端点必须落在环回地址（含 127.0.0.1），
    绝不含被植入的真实主机；ambient 的 RERANKER_API_KEY 不得原样穿透（要么不存在、
    要么已是探针假 key）。
    """
    hostile_base = "https://api.hostile-ambient.test/v1"  # 虚构主机，非任何真实凭据
    hostile_key = "hostile-ambient-reranker-key"
    monkeypatch.setenv("OPENAI_BASE_URL", hostile_base)
    monkeypatch.setenv("RERANKER_BASE_URL", hostile_base)
    monkeypatch.setenv("RERANKER_API_KEY", hostile_key)
    env = RC.isolated_probe_env(str(tmp_path / "probe_home"))
    for key in ("OPENAI_BASE_URL", "RERANKER_BASE_URL"):
        val = env.get(key, "")
        assert "127.0.0.1" in val, f"{key} 未钉到黑洞端点：探针可能向真实端点出网（当前 {val!r}）"
        assert hostile_base not in val, f"{key} 仍指向被植入的真实主机——ambient 端点穿透"
    assert env.get("RERANKER_API_KEY", RC._PROBE_KEY) != hostile_key, (
        "ambient RERANKER_API_KEY 原样穿透进探针 env——真实凭据进入探针子进程"
    )


def test_schema_drift_issues_from_payload_reports_every_class():
    payload = {
        "migration_failures": [{"step": 42, "error": "boom"}],
        "missing_tables": ["ghosttable"],
        "drift": ["memoryitem.shared_scope model=False db=True"],
        "tables_checked": 3,
    }
    issues = RC.schema_drift_issues_from_payload(payload)
    assert len(issues) == 3, issues
    assert any("迁移失败" in i for i in issues)
    assert any("缺表" in i for i in issues)
    assert any("shared_scope" in i for i in issues), f"漂移必须点名到列：{issues}"


def test_schema_drift_issues_from_clean_payload_is_empty():
    payload = {
        "migration_failures": [],
        "missing_tables": [],
        "drift": [],
        "tables_checked": 120,
    }
    assert RC.schema_drift_issues_from_payload(payload) == []


def test_schema_drift_probe_really_builds_schema_in_tmp_home():
    """真执行：临时 LANTAI_HOME 里跑一次 init_db()，回报规模、隔离性与漂移面。

    规模判据取「探针子进程自己声明的表数 vs 实际建出的表数」——**不与父进程比**：
    父进程的 `SQLModel.metadata` 会被同会话里其它用例导入的模型撑大，一比就变成
    顺序敏感的比较（实测：单独跑本文件绿，带上别的文件一起跑就红）。
    末段是回归哨兵（口径同 `test_lint_gate_passes_on_real_repo`）：今天的库必须
    迁移无失败、不缺表、列/NULL 约束与模型一致。
    """
    payload = RC.schema_drift_probe(RC.REPO_ROOT)
    assert payload["home_isolated"], f"探针必须跑在临时 LANTAI_HOME：{payload}"
    assert payload["tables_checked"] > 30, f"建表规模不像全库：{payload}"
    assert payload["tables_built"] >= payload["tables_checked"], (
        f"探针声明的表没有全建出来：{payload}"
    )
    assert RC.schema_drift_issues_from_payload(payload) == [], (
        f"迁移/约束漂移：{payload['migration_failures']} "
        f"{payload['missing_tables']} {payload['drift']}"
    )


def test_tripwire_report_issues_fail_closed():
    """绊线报告缺文件 / 没装载 / 命中宿主库，三种都必须阻；干净放行。"""
    assert RC.tripwire_report_issues("", home="/host/remembrance.db"), "空报告被放行了"
    assert any("未装载" in i for i in RC.tripwire_report_issues("[miss] ok\n", home="/h")), (
        "绊线没装载也被放行了"
    )
    hits = RC.tripwire_report_issues("[TRIPEWIRE-ARMED] x\n[HIT] test=a connect(b)\n", home="/h")
    assert any("真实数据目录" in i for i in hits), hits
    assert RC.tripwire_report_issues("[TRIPEWIRE-ARMED] /h\n[miss] x\n", home="/h") == []


def test_tripwire_watch_must_be_armed_on_the_host_db():
    """回归锁（票 05）：绊线武装在「子进程自己的 home」上时，[HIT]=0 是恒真的。

    实测事故：把门禁/全量跑在注入了临时 LANTAI_HOME 的环境里，报告变成
    `[TRIPEWIRE-ARMED] <沙箱>/remembrance.db`，而宿主库是另一个目录——没有任何
    人盯宿主库，任何一次真实泄漏都会被读成「干净」。看护对象必须与宿主库一致；
    watch 未知（CI 无宿主库可指）时不新增判据，避免误杀。
    """
    sandbox = os.path.join("c:", "tmp", "sandbox", "remembrance.db")
    host = os.path.join("c:", "host", "remembrance-data", "remembrance.db")
    bad = f"[TRIPEWIRE-ARMED] {sandbox}\n[miss] x\n"
    issues = RC.tripwire_report_issues(bad, home="(未指明)", watch=host)
    assert any("看护" in i for i in issues), f"绊线武装在沙箱却被判干净：{issues}"
    good = f"[TRIPEWIRE-ARMED] {host}\n[miss] x\n"
    assert RC.tripwire_report_issues(good, home=r"c:\host", watch=host) == []
    assert RC.tripwire_report_issues(bad, home="(未指明)", watch="") == []


def test_host_home_for_watch_is_never_unknown(tmp_path, monkeypatch):
    """宿主库路径必须由门禁在父进程侧解析出来，不能依赖被子进程重定向的 home。

    本例只锁「无显式声明 => 回落 settings 默认」这一条分支，必须清掉
    `LANTAI_GATE_HOST_HOME`：门禁/CI 口径跑全量时运行器会显式声明宿主库（让绊线盯
    住宿主库而不是沙箱），ambient 值一旦优先返回，断言就被环境牵着走——实证：票 05
    续6 首轮 CI 口径全量 1 failed 正是这条（与顺序无关，纯环境泄漏）。
    """
    monkeypatch.delenv("LANTAI_HOME", raising=False)
    monkeypatch.delenv("REMEMBRANCE_HOME", raising=False)
    monkeypatch.delenv("LANTAI_GATE_HOST_HOME", raising=False)
    home = RC.host_home_for_watch(tmp_path)
    assert home.strip(), "返回空 => 绊线只能看护沙箱，[HIT]=0 不可证明"
    from lantai.core.settings import settings

    assert os.path.normcase(str(settings.LANTAI_HOME)) == os.path.normcase(home)


# ── 票 05 续6：三处「旗标 / 空值 / 沙箱自看护」合成绿灯的回归锁 ──


def test_summarize_treats_skip_as_blocking_not_as_pass():
    """回归锁：跳过一项 = 少一项证据，不能读成「全部通过」。

    实测事故（票 05 本轮复现）：`release_check.py v0.66.0 --skip-suite
    --allow-mcp-full-exposure --allow-dirty` 打印两行 [SKIP] 仍报
    「结果: 4/4 通过, 0 失败」并 exit 0 —— 旗标可以合成绿灯，门禁自证失效。
    """
    judged = [
        (RC.PASS, "CI lint 门禁", ""),
        (RC.PASS, "版本一致性", ""),
        (RC.PASS, "Git 状态", ""),
        (RC.PASS, "迁移与列约束漂移", ""),
    ]
    line, code = RC.summarize(judged, n_skip=0)
    assert code == 0, f"全判定且无失败时必须放行：{line}"
    assert "4/4 通过" in line, line

    line, code = RC.summarize(judged, n_skip=2)
    assert code != 0, f"存在 [SKIP] 仍 exit 0 = 旗标可合成绿灯：{line}"
    assert "跳过" in line, f"汇总必须显式写出跳过项数：{line}"
    assert "4/6" in line, f"分母必须含未判定的检查项：{line}"

    mixed = judged[:2] + [(RC.FAIL, "MCP 默认暴露", "全量暴露")]
    line, code = RC.summarize(mixed, n_skip=1)
    assert code != 0, line
    assert "1 失败" in line and "1 跳过" in line, line

    inlist = [(RC.PASS, "a", ""), (RC.SKIP, "b", "旗标跳过")]
    line, code = RC.summarize(inlist)
    assert code != 0, f"SKIP 记进 results 也被当成通过：{line}"


def test_mcp_probe_visibility_issue_blocks_unreadable_registry():
    """回归锁：探针读到 0 个注册工具 = 看不见默认面，不得当成「已收窄」。

    判据 `if total and active >= total` 让 `mcp_exposure_issue(0, 0)` 返回 None；
    探针本身不回报或跑不起来都已经阻（fail-closed），唯独 `MCPCOUNT 0 0` 漏网——
    注册表被改名/导入到半个模块时会伪装成「默认暴露已收窄」。
    """
    assert RC.mcp_probe_visibility_issue(0, 0) is not None, "空注册表被放行 => 看不见默认面也 GO"
    assert RC.mcp_probe_visibility_issue(3, 0) is not None
    assert RC.mcp_probe_visibility_issue(96, 96) is None, (
        "全量暴露仍由 mcp_exposure_issue 阻断，不在此处重复计"
    )
    assert RC.mcp_probe_visibility_issue(5, 96) is None, "已收窄不该在这里新增误杀"


def test_host_home_for_watch_prefers_declared_host_home(tmp_path, monkeypatch):
    """回归锁：运行器把进程 env 沙箱化后，绊线仍必须盯住维护者声明的宿主库。

    若 `LANTAI_HOME` 被注入临时目录且没有显式声明宿主库，`host_home_for_watch()`
    会回落到 settings 推导值——在沙箱化环境里那正是沙箱自己，watch == armed，
    [HIT]=0 变成恒真（vacuous-but-pass）。`LANTAI_GATE_HOST_HOME` 必须优先，
    才能让「跑在隔离 home 里」与「证明没碰宿主库」同时成立。
    """
    declared = str(tmp_path / "host-declared" / "remembrance-data")
    monkeypatch.setenv("LANTAI_GATE_HOST_HOME", declared)
    monkeypatch.setenv("LANTAI_HOME", str(tmp_path / "sandbox-home"))
    monkeypatch.setenv("REMEMBRANCE_HOME", str(tmp_path / "stale-legacy-home"))
    assert RC.host_home_for_watch(tmp_path) == declared, (
        "显式声明的宿主库被 ambient LANTAI_HOME 覆盖 => 绊线看护沙箱自己"
    )


def test_allow_dirty_records_skip_not_pass(clean_repo):
    """回归锁：`--allow-dirty` 只能留下「跳过」痕迹，不得再冒充 Git 状态 PASS。

    实测（票 05 续6 修复前）：`--skip-suite --allow-mcp-full-exposure --allow-dirty`
    打印 `[PASS] Git 状态 — master / tag 不存在 / origin 存在（工作区未检查）`，
    一个根本没被看过的检查项计入通过数；旧签名里也没有任何通道把跳过传给汇总
    （`git_issues(..., skips=[])` 直接 TypeError）。
    """
    assert RC.git_issues(clean_repo, "0.3.8", allow_dirty=False) == []

    (clean_repo / "uncommitted.txt").write_text("x", encoding="utf-8")
    skips: list[str] = []
    issues = RC.git_issues(clean_repo, "0.3.8", allow_dirty=True, skips=skips)
    assert issues == [], f"--allow-dirty 仍把脏工作区报成硬失败：{issues}"
    assert skips == ["Git 状态"], "脏工作区被跳过必须留下可计数的痕迹"

    line, code = RC.summarize([(RC.PASS, "版本一致性", "")], n_skip=len(skips))
    assert code != 0, f"跳过 Git 状态后仍能 GO：{line}"

    (clean_repo / "uncommitted.txt").unlink()
    clean_skips: list[str] = []
    assert RC.git_issues(clean_repo, "0.3.8", allow_dirty=True, skips=clean_skips) == []
    assert clean_skips == [], "工作区本就干净时不该记跳过（不给门禁新增误杀）"


# ── 票 05 续7：门禁自身的「skip 当 pass / 空跑当绿灯」漏网 ──


def test_suite_counts_parses_pytest_tail_line():
    """计数必须来自 pytest 收尾行，供门禁把证据写进日志（不再只说「退出码 0」。"""

    assert RC.suite_counts(".... [100%]\n2743 passed, 5 skipped, 2 warnings in 384.08s\n") == {
        "passed": 2743,
        "skipped": 5,
    }

    assert RC.suite_counts("1 skipped in 0.05s\n") == {"skipped": 1}

    assert RC.suite_counts("5 failed, 10 passed in 2.00s\n") == {"failed": 5, "passed": 10}

    assert RC.suite_counts("1 error in 0.30s\n") == {"error": 1}

    assert RC.suite_counts("no tests ran in 0.01s\n") == {}, "空跑必须解析成「没有计数」"

    assert RC.suite_counts("random noise without summary\n") == {}


def test_suite_summary_issue_requires_real_passes():
    """结构性判据：退出码 0 不算数，必须至少有一条真跑过的通过用例。



    不含可调阈值：只问「passed 是否为 0 / 有没有收尾计数行」，不比较总数、不设比例。

    """

    assert RC.suite_summary_issue("1 passed in 0.10s\n") is None

    assert RC.suite_summary_issue("2743 passed, 5 skipped in 384.08s\n") is None, (
        "有真实通过就有证据：5 条 skip 只要求写进日志，不新增误杀"
    )

    issue = RC.suite_summary_issue("1 skipped in 0.05s\n")

    assert issue and "没有任何真实通过" in issue, f"全 skip 被放行了：{issue}"

    issue = RC.suite_summary_issue("no tests ran in 0.01s\n")

    assert issue and "收尾计数行" in issue, f"空跑被放行了：{issue}"


def test_suite_gate_blocks_when_nothing_actually_ran(tmp_path):
    """决定性复现（修复前为 [] —— 假绿）：整个项目只有 skip，门禁却报「全量测试 PASS」。



    实测：`suite_gate_issues()` 对「1 条 @pytest.mark.skip 用例」的临时项目返回空列表

    ——pytest 退出码 0、绊线照常武装、没人连接宿主库，三条判据全过，于是「一个用例

    都没跑」被读成绿灯。这正是验收底线禁止的 skip 当 pass。

    """

    (tmp_path / "tests").mkdir()

    (tmp_path / "tests" / "test_all_skipped.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason=\x27演示用\x27)\n"
        "def test_never_runs():\n    assert False\n",
        encoding="utf-8",
    )

    issues = RC.suite_gate_issues(tmp_path, timeout=180, real_home=str(tmp_path / "nohome"))

    assert issues, "全 skip 的空跑必须被门禁拦下，不能当成「全量测试通过」"

    assert any("真实通过" in i or "收尾计数行" in i for i in issues), issues


# ── 票 04：命名空间 provenance 门禁（攻击模拟三臂）───────────────────────


def test_namespace_provenance_clean_env_passes():
    """干净环境（PYTHONPATH 清空）下五个哨兵全部落在本仓/活动解释器内。"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    assert RC.namespace_provenance_issues(RC.REPO_ROOT, env=env) == []


def test_namespace_provenance_catches_dependency_binary_shadow(tmp_path):
    """模拟攻击：PYTHONPATH 里的外来 site-packages 遮蔽第三方依赖 → 门禁必须阻断。

    PYTHONPATH 条目在 sys.path 上先于 venv site-packages，外来 pydantic
    （遮蔽体 import 即崩）抢在真依赖之前解析——依赖面是修复 tests 正包化
    之后唯一还敞着的 PYTHONPATH 注入面。
    """
    foreign = tmp_path / "foreign-site"
    (foreign / "pydantic").mkdir(parents=True)
    (foreign / "pydantic" / "__init__.py").write_text(
        "raise ImportError('遮蔽体：外来 pydantic 不得被解析')\n", encoding="utf-8"
    )
    env = {**os.environ, "PYTHONPATH": str(foreign)}
    issues = RC.namespace_provenance_issues(RC.REPO_ROOT, env=env)
    assert issues, "外来依赖遮蔽未被拦截——发布门禁对第三方劫持失守（票 04 红线）"
    assert any("pydantic" in i for i in issues), issues


def test_namespace_provenance_catches_first_party_shadow_pre_fix_world(tmp_path):
    """tripwire 臂：门禁必须判红「tests 未正包化 + 外来正包」的修复前世界。

    本仓正包化 + cwd 首位后，PYTHONPATH 直塞假一方包打不进来（实测：连
    sitecustomize 的 sys.path.insert(0) 都会被 -c 的 cwd 迟插入顶到第 2 位）——
    一方包哨兵的 tripwire 价值在**回归锁**：重建票 04 的病灶世界（受控假仓
    无 tests/__init__.py 的命名空间形态 + 外来正式 tests 包），门禁必须报
    `tests` 解析越界。删掉本仓 tests/__init__.py 的回归等价于此世界。
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "lantai").mkdir(parents=True)
    (fake_repo / "lantai" / "__init__.py").write_text("", encoding="utf-8")
    (fake_repo / "tests").mkdir()  # 刻意不带 __init__.py——命名空间形态
    shadow = tmp_path / "shadow"
    (shadow / "tests").mkdir(parents=True)
    (shadow / "tests" / "__init__.py").write_text("", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(shadow)}
    issues = RC.namespace_provenance_issues(fake_repo, env=env)
    assert issues, "修复前世界（一方命名空间 + 外来正包）未被门禁判红——tripwire 失守"
    assert any("`tests`" in i for i in issues), issues


# ── 文档引用对账哨兵（v0.68 票 09：P0-3 同根因的结构性拦截）────────────────


def test_docs_reference_gate_blocks_missing_script(tmp_path):
    """红样：现役文档引用不存在的 scripts/*.py 必须被判红，且点名具体死链。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("先跑 `scripts/foo.py` 初始化。\n", encoding="utf-8")
    issues = RC.docs_reference_issues(tmp_path)
    assert issues, "引用不存在脚本未被哨兵判红——防复发哨兵失守"
    assert any("scripts/foo.py" in i for i in issues), f"未点名具体死链：{issues}"


def test_docs_reference_gate_passes_on_real_repo():
    """绿样：真仓现役文档的脚本引用全部可解析。"""
    issues = RC.docs_reference_issues(RC.REPO_ROOT)
    assert issues == [], "现役文档存在死链引用：\n" + "\n".join(issues)


def test_docs_reference_gate_whitelists_deleted_entry_annotations(tmp_path):
    """白名单样：「已删除」历史注记（P0-3 修复的合法反指形态）不误放行错、也不误报。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "h.md").write_text(
        "旧入口 `scripts/mcp_server.py` 已删除，勿再引用。\n", encoding="utf-8"
    )
    assert RC.docs_reference_issues(tmp_path) == []


def test_docs_reference_gate_ignores_placeholders(tmp_path):
    """占位符样：<name>.py / probe_*.py / 外链不在扫描面（否则上线首日即误报）。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "p.md").write_text(
        "示例：`<name>.py`、`probe_*.py`，外链 https://example.com/a.py。\n",
        encoding="utf-8",
    )
    assert RC.docs_reference_issues(tmp_path) == []
