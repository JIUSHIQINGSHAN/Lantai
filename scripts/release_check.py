"""发布前检查：版本一致性 + Git 上传前状态（只读，不改文件）。

用法：
  python scripts/release_check.py                 # 以 pyproject.toml 当前版本为目标
  python scripts/release_check.py v0.3.8          # 指定目标版本（v 前缀可选）
  python scripts/release_check.py --allow-dirty   # 允许未提交文件（仅排查用）
  python scripts/release_check.py --skip-suite    # 跳过全量测试（仅排查用；默认必跑）
  python scripts/release_check.py --allow-mcp-full-exposure  # 承认默认全量暴露（仅排查用）
  python scripts/release_check.py v0.3.8 --online # 同时检查远程 origin tag（需网络）

退出码 0 = 可上传，1 = 有问题。

任何旗标（--skip-suite / --allow-mcp-full-exposure / --allow-dirty）都不会换来
0：被跳过的检查等于少一份证据，门禁据此阻断（票 05 续6 实测「旗标合成绿灯」）。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def summarize(results: list[tuple[str, str, str]], n_skip: int = 0) -> tuple[str, int]:
    """汇总门禁结果：FAIL 阻断，SKIP 同样阻断——没取证不等于取到证。

    分母始终按「应当跑过的检查数」算（已判定 + 被跳过），否则 4 项判定 + 2 项跳过
    会被写成「4/4 通过」，读者无从看出证据是残缺的。
    """
    n_fail = sum(1 for status, _, _ in results if status == FAIL)
    n_skipped_in_list = sum(1 for status, _, _ in results if status == SKIP)
    n_skip = n_skipped_in_list + max(0, n_skip)
    n_pass = len(results) - n_fail - n_skipped_in_list
    total = len(results) + n_skip
    if n_skip:
        return (
            f"结果: {n_pass}/{total} 通过, {n_fail} 失败, {n_skip} 跳过"
            "（跳过项未取证，不构成发布证据）",
            1,
        )
    return f"结果: {n_pass}/{total} 通过, {n_fail} 失败", (1 if n_fail else 0)


def announce_skip(skips: list[str] | None, name: str, detail: str) -> None:
    """记下一项被旗标跳过的检查：打印 [SKIP] 并计入调用方持有的清单。

    计数交给调用方的局部 list，不写模块全局——测试里调用这些函数不得污染门禁状态。
    """
    print(f"[{SKIP}] {name} — {detail}")
    if skips is not None:
        skips.append(name)


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


def git_issues(
    repo_root: Path,
    version: str,
    allow_dirty: bool,
    online: bool = False,
    skips: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "master":
        issues.append(f"当前分支 = {branch}，发布分支应为 master")
    status = _git(repo_root, "status", "--porcelain").stdout
    if status.strip():
        if allow_dirty:
            announce_skip(
                skips,
                "Git 状态",
                "工作区不干净 — 已用 --allow-dirty 跳过（未提交内容不在发布物里）",
            )
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


# ── 票 05（v0.66）：四类阻断项——红主干 / 生产全局污染 / 默认全暴露 / 约束漂移 ──
#
# 这一节存在的原因：v0.39–v0.59 共 21 个标签带着红主干发布（评审报告 N-07），而
# 本脚本此前只查「版本引用 + git 干净 + ruff」——**从不跑测试**。「发布门禁通过」
# 与「主干是绿的」从来不是同一件事，门禁是个漏检的橡皮图章。评审点名的另外三类
# 同样无处可拦：默认 MCP 全量暴露（N-04/C-15/R-02，69→94→96）、测试可能连宿主
# 真实库（生产全局污染）、迁移与列约束漂移（D-13）。
#
# 判据一律写成**结构性**规则（默认集是否等于全量注册表、绊线报告里有没有
# `[HIT]`、metadata 与实表的 NULL 约束是否一致），不引入任何可调数字——数字就是
# 「把阈值调大来造假绿」的抓手。探针一律跑在临时 `LANTAI_HOME` 里。

SUITE_TIMEOUT_S = 3600
_TRIPWIRE_PLUGIN = "real_home_tripwire"
_PROBE_KEY = "gate-probe-key"

_MCP_PROBE_CODE = (
    "from lantai.cli import mcp\nprint('MCPCOUNT', len(mcp._get_active_tools()), len(mcp.TOOLS))\n"
)

_SCHEMA_PROBE_CODE = """
import json
import os
from sqlmodel import SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.parameters.trust_models  # noqa: F401
from sqlalchemy import inspect

from lantai.core.settings import settings
from lantai.storage import db
from lantai.storage.migrations import get_migration_failures

db.engine = create_engine(settings.DATABASE_URL)
db.init_db()
insp = inspect(db.engine)
db_tables = set(insp.get_table_names())
meta = SQLModel.metadata.tables
missing = sorted(set(meta) - db_tables)
drift = []
for name, table in meta.items():
    if name not in db_tables:
        continue
    cols = {c["name"]: c for c in insp.get_columns(name)}
    for col in table.columns:
        if col.name not in cols:
            drift.append(f"{name}.{col.name} 建库缺列")
        elif bool(cols[col.name]["nullable"]) != bool(col.nullable):
            drift.append(
                f"{name}.{col.name} NULL 约束漂移 "
                f"model_nullable={col.nullable} db_nullable={cols[col.name]['nullable']}"
            )
payload = {
    "migration_failures": get_migration_failures(),
    "missing_tables": missing,
    "drift": drift,
    "tables_checked": len(meta),
    "tables_built": len(db_tables),
    "home_used": str(settings.LANTAI_HOME),
    "home_isolated": os.path.normcase(str(settings.LANTAI_HOME))
    != os.path.normcase(os.environ.get("LANTAI_GATE_HOST_HOME", "__none__")),
}
print("SCHEMAPAYLOAD" + json.dumps(payload, ensure_ascii=False))
"""


def host_lantai_home(repo_root: Path) -> str:
    """宿主真实数据目录：环境变量优先，其次仓库 `.env` 里的 LANTAI_HOME。"""
    env_home = (os.environ.get("LANTAI_HOME") or "").strip()
    if env_home:
        return env_home
    dotenv = repo_root / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("LANTAI_HOME") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def host_home_for_watch(repo_root: Path) -> str:
    """绊线该盯哪个宿主库——必须在**父进程侧**定下来。

    优先级：`LANTAI_GATE_HOST_HOME`（维护者/运行器显式声明）> ambient
    `LANTAI_HOME` / 仓库 `.env` > settings 推导的机器默认目录。不能拿
    "子进程将被重定向到的 home" 当宿主库：一旦运行器注入了临时 LANTAI_HOME，绊线
    就武装在沙箱上，[HIT]=0 变成恒真——实测踩过（票 05 本轮复现）。显式声明的存在
    正是为了让「跑在隔离 home 里」和「证明没碰宿主库」两件事同时成立。
    """
    declared = (os.environ.get("LANTAI_GATE_HOST_HOME") or "").strip()
    if declared:
        return declared
    home = host_lantai_home(repo_root)
    if home:
        return home
    from lantai.core.settings import settings  # 懒导入：不改变门禁自身的环境语义

    return str(settings.LANTAI_HOME)


def isolated_probe_env(probe_home: str) -> dict[str, str]:
    """探针环境：临时 LANTAI_HOME + 非空 API_KEY（禁 DEV MODE 回退）+ 不设 MCP profile。"""
    os.makedirs(probe_home, exist_ok=True)
    env = os.environ.copy()
    env["LANTAI_HOME"] = probe_home
    env["REMEMENTRANCE_HOME"] = probe_home  # 旧变量名兼容（settings 仍会读它）
    env["DATABASE_URL"] = ""  # 空值 => 由 LANTAI_HOME 推导
    env["API_KEY"] = _PROBE_KEY
    env["OPENAI_API_KEY"] = _PROBE_KEY
    env.pop("LANTAI_MCP_PROFILE", None)
    env.pop("MCP_TOOL_PROFILE", None)
    return env


def real_home_hits(report_text: str) -> list[str]:
    return [ln for ln in report_text.splitlines() if ln.startswith("[HIT]")]


def tripwire_armed_path(report_text: str) -> str:
    """从报告里取绊线实际武装的库路径。"""
    for ln in report_text.splitlines():
        if ln.startswith("[TRIPEWIRE-ARMED]"):
            return ln.split(None, 1)[1].strip() if " " in ln else ""
    return ""


def tripwire_report_issues(report_text: str, *, home: str, watch: str = "") -> list[str]:
    """绊线报告的判读：没报告 / 没装载 / 命中宿主库，三种都必须阻。

    「静默不检测」按红处理——绊线没装载成功时返回空 hits 和返回干净报告长得
    一样，只看 hits 数量就会把「没测」读成「测过且干净」。
    """
    if not report_text.strip():
        return [
            f"宿主数据目录绊线没有产出报告——无法证明测试未连真实库（LANTAI_HOME={home}），不放行"
        ]
    if "[TRIPEWIRE-ARMED]" not in report_text:
        return ["宿主数据目录绊线未装载（报告里没有 [TRIPEWIRE-ARMED]）——没检测不等于干净，不放行"]
    hits = real_home_hits(report_text)
    if hits:
        return [f"测试连接了宿主真实数据目录（LANTAI_HOME={home}）：\n" + "\n".join(hits[:5])]
    if watch:
        armed = tripwire_armed_path(report_text)
        if armed and os.path.normcase(os.path.abspath(armed)) != os.path.normcase(
            os.path.abspath(watch)
        ):
            return [
                f"宿主数据目录绊线看护错了对象：武装在 {armed}，宿主库是 {watch}——"
                "没人盯宿主库，[HIT]=0 只是因为沙箱被当成了看护目标，等于没检测，不放行"
            ]
    return []


def suite_gate_issues(
    repo_root: Path,
    *,
    timeout: int = SUITE_TIMEOUT_S,
    real_home: str | None = None,
    python: str | None = None,
) -> list[str]:
    """全量测试 + 宿主数据目录绊线：红主干、或被测进程连真实库都必须拦下上传。

    与 CI 同口径，但显式清掉 `addopts`（本地 pyproject 的覆盖率参数不该变成
    「到底跑没跑」的隐式变量），并把 `tests/support/real_home_tripwire.py`
    挂成插件（按**连接路径**判定，不按 mtime——开发机上常驻服务自己也在写库）。
    """
    py = python or sys.executable
    home = host_home_for_watch(repo_root) if real_home is None else real_home
    watch = os.path.join(home, "remembrance.db") if home else ""
    support = REPO_ROOT / "tests" / "support"
    with tempfile.NamedTemporaryFile(prefix="lantai_tripwire_", suffix=".txt") as fh:
        report_path = fh.name
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(support), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    env["LANTAI_GATE_REAL_HOME"] = watch
    env["LANTAI_GATE_REPORT"] = report_path
    env.setdefault("API_KEY", _PROBE_KEY)
    cmd = [
        py,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-p",
        _TRIPWIRE_PLUGIN,
    ]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return [f"全量测试超时（>{timeout}s）——超时不是绿灯，不放行"]
    except OSError as exc:
        return [f"全量测试无法执行（{exc}）——跑不动就等于没跑，不放行"]
    try:
        report_text = Path(report_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        report_text = ""

    issues: list[str] = []
    if exit_code != 0:
        summary = next(
            (ln for ln in reversed(output.splitlines()) if re.search(r"\d+ (failed|error)", ln)),
            "",
        )
        failed_lines = [ln for ln in output.splitlines() if ln.startswith("FAILED")]
        issues.append(
            "全量测试未绿（红主干不得上传）："
            + (summary.strip() or "见失败清单")
            + "\n"
            + "\n".join(failed_lines[:40])
        )
    elif "[TRIPEWIRE-ARMED]" not in report_text:
        issues.append("全量测试没有真正跑起来（退出码 0 却没有绊线报告）——不放行")
    issues.extend(tripwire_report_issues(report_text, home=home or "(宿主目录未指明)", watch=watch))
    return issues


def mcp_exposure_issue(active: int, total: int) -> str | None:
    """默认暴露 == 全量注册表 => 阻断。结构性判据，不含可调数字。"""
    if total and active >= total:
        return (
            f"默认 MCP 暴露未收窄：不设 profile 时 tools/list 返回全部 {active}/{total} 个工具"
            "（评审报告 N-04/C-15/R-02：69→94→96 一路涨，默认仍是全量）——阻断发布，"
            "直到维护者显式声明默认策略（改 `_get_active_tools()` 的默认档，"
            "或在部署侧设 LANTAI_MCP_PROFILE 并把它写进发布流程）"
        )
    return None


def mcp_probe_visibility_issue(active: int, total: int) -> str | None:
    """注册表读到 0 个工具 = 探针没看见默认暴露面，不能读成「已收窄」。

    `mcp_exposure_issue()` 的 `if total and ...` 是结构性判据（0/0 确实不构成
    「全量暴露」），但发布证据要的是「默认面可见」；探针跑不起来与不回报都已经
    阻了，唯独 `MCPCOUNT 0 0` 会溜过去——按同一条 fail-closed 口径补上。
    """
    if total > 0:
        return None
    return (
        f"MCP 默认面无法读取：探针回报注册表 {active}/{total} 个工具（0 个 = 没看到工具表，"
        "多半是导入路径/注册表被改名）——无法证明默认暴露未收窄，按 fail-closed 阻断"
    )


def mcp_exposure_issues(
    repo_root: Path, *, python: str | None = None, timeout: int = 240
) -> list[str]:
    py = python or sys.executable
    with tempfile.TemporaryDirectory(prefix="lantai_gate_home_") as probe_home:
        env = isolated_probe_env(probe_home)
        try:
            proc = subprocess.run(  # noqa: S603
                [py, "-c", _MCP_PROBE_CODE],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"MCP 默认暴露探针无法执行（{exc}）——看不见默认面就不放行"]
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0] == "MCPCOUNT" and len(parts) == 3:
            active, total = int(parts[1]), int(parts[2])
            issue = mcp_probe_visibility_issue(active, total) or mcp_exposure_issue(active, total)
            return [issue] if issue else []
    return [
        f"MCP 探针没有回报工具数（exit={proc.returncode}）：" + (proc.stderr or "")[-300:],
    ]


def schema_drift_issues_from_payload(payload: dict) -> list[str]:
    issues = [
        f"迁移失败（红着也不能发布）：{item}" for item in payload.get("migration_failures") or []
    ]
    issues += [f"建库缺表：{name}" for name in payload.get("missing_tables") or []]
    issues += [f"列/约束漂移：{item}" for item in payload.get("drift") or []]
    return issues


def schema_drift_probe(repo_root: Path, *, python: str | None = None, timeout: int = 300) -> dict:
    """临时 LANTAI_HOME 里真跑一次 `init_db()`（建表 + 全量迁移），回报漂移面。"""
    py = python or sys.executable
    probe: dict = {
        "migration_failures": [],
        "missing_tables": [],
        "drift": [],
        "tables_checked": 0,
        "tables_built": 0,
        "home_isolated": False,
    }
    with tempfile.TemporaryDirectory(prefix="lantai_gate_schema_") as probe_home:
        env = isolated_probe_env(probe_home)
        env["LANTAI_GATE_HOST_HOME"] = host_lantai_home(repo_root) or "__none__"
        try:
            proc = subprocess.run(  # noqa: S603
                [py, "-c", _SCHEMA_PROBE_CODE],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe["migration_failures"] = [{"step": "probe", "error": f"探针无法执行：{exc}"}]
            return probe
        line = next(
            (ln for ln in (proc.stdout or "").splitlines() if ln.startswith("SCHEMAPAYLOAD")), ""
        )
    if not line:
        probe["migration_failures"] = [
            {"step": "probe", "error": (proc.stderr or "")[-400:] or f"exit={proc.returncode}"}
        ]
        return probe
    try:
        payload = json.loads(line[len("SCHEMAPAYLOAD") :])
    except ValueError as exc:
        probe["migration_failures"] = [{"step": "probe", "error": f"回报不可解析：{exc}"}]
        return probe
    for key, value in probe.items():
        payload.setdefault(key, value)
    return payload


def schema_drift_issues(repo_root: Path) -> list[str]:
    payload = schema_drift_probe(repo_root)
    issues = schema_drift_issues_from_payload(payload)
    if not payload.get("home_isolated"):
        issues.append("schema 探针没有跑在临时 LANTAI_HOME 里——它自己就可能污染生产，不放行")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前检查（只读）")
    parser.add_argument("version", nargs="?", help="目标版本，默认读 pyproject.toml")
    parser.add_argument("--allow-dirty", action="store_true", help="允许未提交文件（仅排查用）")
    parser.add_argument(
        "--online", action="store_true", help="联网检查远程 origin 是否已存在同名 tag"
    )
    parser.add_argument(
        "--skip-suite", action="store_true", help="跳过全量测试（仅排查用；默认必跑并据此阻断）"
    )
    parser.add_argument(
        "--allow-mcp-full-exposure",
        action="store_true",
        help="承认默认 MCP 全量暴露（仅排查用；默认阻断）",
    )
    parser.add_argument(
        "--suite-timeout", type=int, default=SUITE_TIMEOUT_S, help="全量测试超时秒数"
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

    skips: list[str] = []
    git_failures = git_issues(REPO_ROOT, version, args.allow_dirty, online=args.online, skips=skips)
    for issue in git_failures:
        check("Git 状态", False, issue)
    if not git_failures and "Git 状态" not in skips:
        check("Git 状态", True, "master / 工作区干净 / tag 不存在 / origin 存在")

    mcp_failures = [] if args.allow_mcp_full_exposure else mcp_exposure_issues(REPO_ROOT)
    if args.allow_mcp_full_exposure:
        announce_skip(
            skips, "MCP 默认暴露", "已用 --allow-mcp-full-exposure 跳过（默认面仍需定策）"
        )
    else:
        for issue in mcp_failures:
            check("MCP 默认暴露", False, issue)
        if not mcp_failures:
            check("MCP 默认暴露", True, "默认 tools/list 不等于全量注册表")

    schema_failures = schema_drift_issues(REPO_ROOT)
    for issue in schema_failures:
        check("迁移与列约束漂移", False, issue)
    if not schema_failures:
        check(
            "迁移与列约束漂移",
            True,
            "临时 LANTAI_HOME 内 init_db() 全链通过，metadata 与实表列/NULL 约束一致",
        )

    if args.skip_suite:
        announce_skip(
            skips, "全量测试 + 宿主目录绊线", "已用 --skip-suite 跳过（仅排查用，不代表可发布）"
        )
    else:
        print("[....] 全量测试 + 宿主数据目录绊线进行中（约数分钟）…")
        suite_failures = suite_gate_issues(REPO_ROOT, timeout=args.suite_timeout)
        for issue in suite_failures:
            check("全量测试 + 宿主目录绊线", False, issue)
        if not suite_failures:
            check(
                "全量测试 + 宿主目录绊线",
                True,
                "pytest tests/ 退出码 0，且全程未连接宿主真实 LANTAI_HOME",
            )

    print("=" * 60)
    line, code = summarize(results, n_skip=len(skips))
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
