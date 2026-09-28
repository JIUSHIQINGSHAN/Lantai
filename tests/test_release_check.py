"""发布门禁检查测试：纯函数不 mock + 临时 git 仓库真实执行。"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_RELEASE_CHECK = Path(__file__).parent.parent / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check", _RELEASE_CHECK)
RC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RC)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
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
