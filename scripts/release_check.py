"""发布前检查：版本一致性 + Git 上传前状态（只读，不改文件）。

用法：
  python scripts/release_check.py                 # 以 pyproject.toml 当前版本为目标
  python scripts/release_check.py v0.3.8          # 指定目标版本（v 前缀可选）
  python scripts/release_check.py --allow-dirty   # 允许未提交文件（仅排查用）
  python scripts/release_check.py v0.3.8 --online # 同时检查远程 origin tag（需网络）

退出码 0 = 可上传，1 = 有问题。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

VERSION_REF_SPECS = [
    ("pyproject.toml", "pyproject.toml", re.compile(r'^version\s*=\s*"([^"]+)"', re.M)),
    ("README 版本徽章", "README.md", re.compile(r"badge/version-([0-9.]+)-blue")),
    ("README Docker 示例", "README.md", re.compile(r"lantai:([0-9.]+)")),
    (
        "FastAPI /docs 版本",
        "lantai/api/app.py",
        re.compile(r'FastAPI\(title="[^"]+",\s*version="([0-9.]+)"'),
    ),
    (
        "MCP serverInfo 版本",
        "lantai/cli/mcp.py",
        re.compile(r'"serverInfo":\s*\{[^}]*"version":\s*"([0-9.]+)"'),
    ),
]

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def normalize_version(raw: str) -> str:
    return raw[1:] if raw.startswith("v") else raw


def collect_version_refs(repo_root: Path) -> list[tuple[str, str, str]]:
    """返回 [(label, 相对路径, 版本值)]，一个文件多处引用会逐处返回。"""
    refs: list[tuple[str, str, str]] = []
    for label, rel, pattern in VERSION_REF_SPECS:
        path = repo_root / rel
        if not path.exists():
            refs.append((f"{label}（缺失）", rel, "<missing>"))
            continue
        matches = pattern.findall(path.read_text(encoding="utf-8"))
        if not matches:
            refs.append((label, rel, "<not found>"))
        else:
            for i, value in enumerate(matches, 1):
                suffix = f" #{i}" if len(matches) > 1 else ""
                refs.append((f"{label}{suffix}", rel, value))
    return refs


def changelog_snapshot(text: str) -> tuple[bool, str | None]:
    """返回 (是否有 [Unreleased], 最新已发布版本段)。"""
    unreleased = "## [Unreleased]" in text
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
    return unreleased, (m.group(1) if m else None)


def check_version_consistency(
    version: str,
    refs: list[tuple[str, str, str]],
    changelog_text: str | None,
) -> list[str]:
    issues: list[str] = []
    for label, rel, value in refs:
        if value != version:
            issues.append(f"{label}（{rel}）= {value}，期望 {version}")
    if changelog_text is not None:
        unreleased, latest = changelog_snapshot(changelog_text)
        if not unreleased:
            issues.append("CHANGELOG 缺 [Unreleased] 段")
        if latest != version:
            issues.append(f"CHANGELOG 最新发布段 = {latest}，期望 {version}")
    return issues


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _find_ruff(repo_root: Path) -> str:
    """定位 ruff：本仓 .venv → 系统 PATH → 当前解释器的 Scripts 目录。

    第三个来源是为了让**临时目录**（测试造的 tmp_path）也能找到本仓的
    ruff——`sys.executable` 在测试里就是 `.venv/Scripts/python.exe`，
    同目录下的 `ruff.exe` 就是同一个环境装的那一个。
    """
    candidates = [
        repo_root / ".venv" / "Scripts" / "ruff.exe",
        repo_root / ".venv" / "bin" / "ruff",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which("ruff")
    if found:
        return found
    beside = Path(sys.executable).parent / ("ruff.exe" if os.name == "nt" else "ruff")
    return str(beside) if beside.exists() else ""


def lint_gate_issues(repo_root: Path) -> list[str]:
    """CI 门禁自查：`ruff check` + `ruff format --check`（票 21）。

    为什么加这一步：`.github/workflows/tests.yml` 的 test job 里 lint 步骤
    **排在**全量 pytest 与遗忘质量门禁**前面**。lint 一红，后面两步一步都不跑
    ——所以「本地 pytest 全绿」从来不等于「CI 绿」。上一轮 `.scratch/ci-lint-gate/`
    把 73 条违规清零让门禁第一次真跑起来，之后 4 个提交又把格式带回红
    （`6c07da7d` 起，累积 9 个文件），全量测试在 CI 上又静默停跑了 5 个提交。

    只查 `ruff` 是否可用 + 是否干净；ruff 未安装时 SKIP 而不是 FAIL
    （发布检查不该因为缺一个开发期工具就拦下上传）。
    """
    ruff = _find_ruff(repo_root)
    if not ruff:
        print(f"[{SKIP}] ruff 不可用 — 跳过 lint 自查（装 dev 依赖可启用）")
        return []

    issues: list[str] = []
    for label, args in (
        ("ruff check", ["check", "lantai/", "tests/", "scripts/"]),
        ("ruff format --check", ["format", "--check", "lantai/", "tests/", "scripts/"]),
    ):
        r = subprocess.run(
            [str(ruff), *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            tail = "\n".join((r.stdout or "").splitlines()[-12:])
            issues.append(f"{label} 未通过（CI 的 lint 步骤会红，其后 pytest 一步不跑）：\n{tail}")
    return issues


def git_issues(repo_root: Path, version: str, allow_dirty: bool, online: bool = False) -> list[str]:
    issues: list[str] = []
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "master":
        issues.append(f"当前分支 = {branch}，发布分支应为 master")
    status = _git(repo_root, "status", "--porcelain").stdout
    if status.strip():
        if allow_dirty:
            print(f"[{SKIP}] 工作区不干净 — 已用 --allow-dirty 跳过")
        else:
            issues.append("工作区不干净：\n" + status.strip())
    tags = _git(repo_root, "tag", "--list", f"v{version}").stdout.strip()
    if tags:
        issues.append(f"tag v{version} 已存在")
    remotes = _git(repo_root, "remote").stdout.split()
    if "origin" not in remotes:
        issues.append("缺少远程 origin，无法上传")
    elif online:
        ls = _git(
            repo_root, "ls-remote", "--tags", "origin", f"refs/tags/v{version}"
        ).stdout.strip()
        if ls:
            issues.append(f"远程 origin 已存在 tag v{version}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前检查（只读）")
    parser.add_argument("version", nargs="?", help="目标版本，默认读 pyproject.toml")
    parser.add_argument("--allow-dirty", action="store_true", help="允许未提交文件（仅排查用）")
    parser.add_argument(
        "--online", action="store_true", help="联网检查远程 origin 是否已存在同名 tag"
    )
    args = parser.parse_args(argv)

    # lint 自查放在最前（票 21）：它红 = CI 的全量测试一步不跑，
    # 这时再查版本/tag 没有意义——先让人看见门禁本身是红的。
    lint_failures = lint_gate_issues(REPO_ROOT)
    for issue in lint_failures:
        check("CI lint 门禁", False, issue)
    if not lint_failures:
        check("CI lint 门禁", True, "ruff check + ruff format --check 均通过")

    refs = collect_version_refs(REPO_ROOT)
    pyproject_version = next((v for label, _, v in refs if label == "pyproject.toml"), "")
    version = normalize_version(args.version) if args.version else pyproject_version

    print("=" * 60)
    print(f"发布前检查：v{version}")
    print("=" * 60)

    if not SEMVER_RE.match(version):
        check("目标版本格式", False, f"期望 X.Y.Z，收到 {version!r}")
        print("结果: 失败")
        return 1

    changelog_path = REPO_ROOT / "CHANGELOG.md"
    changelog_text = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    consistency_issues = check_version_consistency(version, refs, changelog_text)
    if consistency_issues:
        for issue in consistency_issues:
            check("版本一致性", False, issue)
    else:
        check("版本一致性", True, f"{len(refs)} 处版本引用 + CHANGELOG 最新段 = v{version}")

    git_failures = git_issues(REPO_ROOT, version, args.allow_dirty, online=args.online)
    for issue in git_failures:
        check("Git 状态", False, issue)
    if not git_failures:
        detail = "master / tag 不存在 / origin 存在"
        if args.allow_dirty:
            detail += "（工作区未检查）"
        check("Git 状态", True, detail)

    print("=" * 60)
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    print(f"结果: {len(results) - n_fail}/{len(results)} 通过, {n_fail} 失败")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
