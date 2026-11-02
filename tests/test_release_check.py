"""发布门禁检查测试：纯函数不 mock + 临时 git 仓库真实执行。"""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_RELEASE_CHECK = Path(__file__).parent.parent / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check", _RELEASE_CHECK)
RC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RC)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _ensure_real_git_available() -> None:
    """git 可能已装但不在 PATH（便携版安装）：定位真实 git.exe 补进 PATH。

    只补环境、不 mock——用例仍真实执行 git 子进程（意图不变）；
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
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return
    pytest.skip("本机无 git 可执行文件：release_check 的 git 用例无法真实执行")


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    _ensure_real_git_available()
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
    for issue in issues:
        assert "未收窄" in issue, f"阻断项必须点名病灶与口径：{issues}"


def test_isolated_probe_env_isolates_home_and_forbids_dev_mode(tmp_path):
    home = str(tmp_path / "probe_home")
    env = RC.isolated_probe_env(home)
    assert os.path.normcase(env["LANTAI_HOME"]) == os.path.normcase(home)
    assert env.get("API_KEY"), "探针需要非空 API_KEY（空则 DEV MODE 回退掩盖真实分支）"
    assert "LANTAI_MCP_PROFILE" not in env, "探针必须模拟『不设 profile』的默认暴露现场"


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
    assert RC.tripwire_report_issues(good, home="c:\host", watch=host) == []
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
