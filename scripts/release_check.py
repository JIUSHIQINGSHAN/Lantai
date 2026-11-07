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
import zipfile
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
    # 票 05 续18：被提交的依赖锁文件同样是发布元数据。0.66.0 bump 只改了上面 5 处
    # tracked 引用，`uv.lock` 里根包版本仍停在 0.65.0（外部校验器 `uv lock --locked
    # --offline` 实测 exit 1），而门禁从不读它 —— 于是漂移一路被当作 PASS 放过。
    # 锚定 name+version 两行，只取根包（`name = "lantai"` 精确闭合引号，不会误抓
    # lantai-* 之类同前缀包）。
    (
        "uv.lock 根包版本",
        "uv.lock",
        re.compile(r'^name = "lantai"\nversion = "([^"]+)"', re.M),
    ),
]

results: list[tuple[str, str, str]] = []

# 红灯必须自带诊断（票 05 续19 D3）：拿到一条 [FAIL] 就得当场知道「哪一段红、
# 属于哪类根因、用哪条最小命令复现」，而不是回头翻门禁代码猜。分类口径固定成词表，
# test_diagnosis_entries_use_declared_vocabulary 锁住不许跑野口径。
DIAGNOSIS_CATEGORIES = (
    "测试污染",
    "约束漂移",
    "依赖缺失",
    "逻辑缺陷",
    "契约/策略违反",
    "发布元数据漂移",
    "仓库状态",
    "环境/工具缺失",
)

# 门禁项名 -> (根因分类, 最小复现命令)。main() 实际判定的每一项都必须在这里登记，
# 漏一项由 test_every_gate_item_exercised_by_main_has_a_diagnosis_entry 判红。
FAILURE_DIAGNOSIS: dict[str, tuple[str, str]] = {
    "CI lint 门禁": (
        "依赖缺失",
        "ruff check lantai/ tests/ scripts/ && ruff format --check lantai/ tests/ scripts/",
    ),
    "CI 步骤覆盖核对": (
        "契约/策略违反",
        "python scripts/release_check.py <版本> --skip-suite",
    ),
    "目标版本格式": ("发布元数据漂移", "python scripts/release_check.py --help"),
    "版本一致性": (
        "发布元数据漂移",
        "python -m pytest tests/test_release_check.py -q -k=version",
    ),
    "构建产物（wheel）": (
        "逻辑缺陷",
        "python -m build --wheel --no-isolation --outdir <临时目录>",
    ),
    "Git 状态": ("仓库状态", "git status --porcelain && git tag --list 'v*'"),
    "命名空间 provenance": (
        "环境/工具缺失",
        "echo %PYTHONPATH% 清空后复跑（宿主 PYTHONPATH 渗入）；细项见 docs/release-process.md 第 9 条",
    ),
    "MCP 默认暴露": (
        "契约/策略违反",
        'python -c "from lantai.cli import mcp; print(len(mcp._get_active_tools()), len(mcp.TOOLS))"',
    ),
    "迁移与列约束漂移": ("约束漂移", "python -m pytest tests/test_legacy_schema_parity.py -q"),
    "老库升级回放与列对齐": ("约束漂移", "python scripts/legacy_parity_probe.py"),
    "全量测试 + 宿主目录绊线": ("测试污染", 'python -m pytest tests/ -q -o addopts=""'),
    "遗忘质量离线门禁": ("逻辑缺陷", "python scripts/run_forgetting_quality.py --check"),
}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        # 失败阶段 / 根因分类 / 最小复现三项齐了才算可诊断的红灯；清单缺项也要显式
        # 标「未分类」，不许静默少一段（那正是「看不见就等于没有」的老毛病）。
        category, repro = FAILURE_DIAGNOSIS.get(
            name,
            (
                "未分类（门禁诊断清单缺该项）",
                f"python scripts/release_check.py <版本> 后定位「{name}」",
            ),
        )
        print(f"    └ 失败阶段={name}｜根因分类={category}｜最小复现={repro}")


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
    if changelog_text is None:
        # 票 05 续46：CHANGELOG.md 取不到文本（缺失/改名/路径错）以前整段静默跳过，
        # 版本一致性照样 PASS——门禁最古老的假绿形态「看不见就等于没有」。
        # 与 addopts 检查同口径（:1038「依据缺失 = 未取证，不放行」）：依据必须在场。
        issues.append("读不到 CHANGELOG.md：CHANGELOG 维度未取证——没取证不等于取到证，不放行")
    else:
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


def lint_gate_issues(repo_root: Path, *, skips: list[str] | None = None) -> list[str]:
    """CI 门禁自查：`ruff check` + `ruff format --check`（票 21）。

    为什么加这一步：`.github/workflows/tests.yml` 的 test job 里 lint 步骤
    **排在**全量 pytest 与遗忘质量门禁**前面**。lint 一红，后面两步一步都不跑
    ——所以「本地 pytest 全绿」从来不等于「CI 绿」。上一轮 `.scratch/ci-lint-gate/`
    把 73 条违规清零让门禁第一次真跑起来，之后 4 个提交又把格式带回红
    （`6c07da7d` 起，累积 9 个文件），全量测试在 CI 上又静默停跑了 5 个提交。

    只查 `ruff` 是否可用 + 是否干净；ruff 未安装时 SKIP 而不是 FAIL
    （发布检查不该因为缺一个开发期工具就拦下上传）。但 SKIP 必须进 `skips` 账：
    没跑过的检查不等于跑过且通过——票 05 续19 实测缺 ruff 时这里只打印一行提示就
    `return []`，main() 随即据此打印 `[PASS] CI lint 门禁`，门禁自己造了个假绿。
    """
    ruff = _find_ruff(repo_root)
    if not ruff:
        announce_skip(skips, "CI lint 门禁", "ruff 不可用 — lint 自查未执行（装 dev 依赖可启用）")
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
# 探针出网黑洞：与测试侧 harness_state 同口径——环回 + discard(9)，任何进程都无从
# 经它出网。失败即断网，不是拿着假 key 向真实白名单端点发出请求再 401（票 05 续27）。
_PROBE_BLACKHOLE_URL = "http://127.0.0.1:9/v1"

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
    """探针环境：临时 LANTAI_HOME + 非空 API_KEY（禁 DEV MODE 回退）+ 不设 MCP profile
    + 出网端点钉到环回黑洞。

    黑洞这一维不是可选加固而是必须（票 05 续27）：只把两把 key 中和成假 key、却让
    ambient / 仓根 `.env`（settings 经 env_file 加载）的真实 `OPENAI_BASE_URL`、
    `RERANKER_BASE_URL`、`RERANKER_API_KEY` 原样穿透，探针子进程就可能拿着假 key
    **向真实白名单端点发出请求**——401 之前请求已经出网。与测试侧 harness_state
    「失败即断网」同口径钉成 `127.0.0.1:9`：真有漏网的调用就是连接被拒，不产生外传。
    """
    os.makedirs(probe_home, exist_ok=True)
    env = os.environ.copy()
    env["LANTAI_HOME"] = probe_home
    env["REMEMBRANCE_HOME"] = probe_home  # 旧变量名兼容（settings 仍会读它）
    env["DATABASE_URL"] = ""  # 空值 => 由 LANTAI_HOME 推导
    env["API_KEY"] = _PROBE_KEY
    env["OPENAI_API_KEY"] = _PROBE_KEY
    env["OPENAI_BASE_URL"] = _PROBE_BLACKHOLE_URL  # 钉黑洞：假 key + 真实端点=已出网
    env["RERANKER_BASE_URL"] = _PROBE_BLACKHOLE_URL
    env["RERANKER_API_KEY"] = _PROBE_KEY  # 空则回退 OPENAI_API_KEY，同口径置假
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


_SUITE_COUNT_RE = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)\b")
_SUITE_EMPTY_RE = re.compile(r"no tests ran|collected 0 items")


def suite_counts(output: str) -> dict[str, int]:
    """从 pytest 收尾行取计数（最后一条命中行）；取不到就返回空 dict = 没有证据。"""
    for line in reversed(output.splitlines()):
        found = _SUITE_COUNT_RE.findall(line)
        if found or _SUITE_EMPTY_RE.search(line):
            counts: dict[str, int] = {}
            for num, kind in found:
                key = "error" if kind.startswith("error") else kind
                counts[key] = counts.get(key, 0) + int(num)
            return counts
    return {}


def suite_summary_issue(output: str) -> str | None:
    """退出码 0 还不够：必须至少有一条真跑过的通过用例，空跑/skip 不算测试通过。

    判据是结构性的（passed 是否为 0、有没有收尾计数行），不含可调阈值、不比比例，
    因此不会因为「把 skip 白名单调大」而失效。
    """
    counts = suite_counts(output)
    if not counts:
        return (
            "全量测试没有可解析的收尾计数行（退出码 0 却拿不到 passed/failed/skipped）"
            "——证据缺失，不放行"
        )
    if not counts.get("passed"):
        return (
            f"全量测试没有任何真实通过的用例：{counts}——skip/空跑不等于测试通过，不放行"
            "（要豁免某条评测线就得写成显式白名单，不能靠 skip 蒙过门禁）"
        )
    return None


# 票 04：命名空间 provenance 哨兵集——第一方入口 + 套件依赖链最深的三方根。
_NAMESPACE_PROBE_MODULES = ("lantai", "tests", "pydantic", "sqlmodel", "fastapi")

_NAMESPACE_PROBE_SRC = """\
import importlib, json, sys
names = json.loads(sys.argv[1])
out = {
    "prefix": sys.prefix,
    "base_prefix": getattr(sys, "base_prefix", sys.prefix),
    "modules": {},
    "errors": {},
}
for name in names:
    try:
        mod = importlib.import_module(name)
        out["modules"][name] = getattr(mod, "__file__", None)
    except BaseException as exc:  # 遮蔽体的任何崩法（ImportError/二进制不兼容）都要如实带出
        out["errors"][name] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""


def _path_under(path: str, base: Path) -> bool:
    norm = os.path.normcase(os.path.realpath(path))
    base_norm = os.path.normcase(os.path.realpath(str(base)))
    return norm == base_norm or norm.startswith(base_norm + os.sep)


def namespace_provenance_issues(repo_root: Path, *, env: dict[str, str] | None = None) -> list[str]:
    """关键 import 的解析落点必须在本仓或活动解释器内（票 04），越界即阻断上传。

    敌意 PYTHONPATH（宿主拉起的会话）从两个位次遮蔽：正式包压命名空间（tests
    面，conftest 收集即死）、PYTHONPATH 先于 venv site-packages（pydantic 等
    三方依赖面——实测 hermes venv 的 pydantic 抢解析直接二进制不兼容崩）。
    CI 跑在干净环境永远探不到这两条，而发布门禁恰恰运行在维护者本机——
    宿主会话里不拦，就是把「跑在别人的代码上」当绿灯发布。探针故意继承当前
    环境（要探的就是门禁自己所在的会话）；任何哨兵解析到本仓/活动解释器
    之外、或 import 即崩，都判红并给出可操作文案（清 PYTHONPATH 复跑）。
    """
    run_env = os.environ.copy() if env is None else dict(env)
    try:
        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                _NAMESPACE_PROBE_SRC,
                json.dumps(list(_NAMESPACE_PROBE_MODULES)),
            ],
            cwd=str(repo_root),
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"命名空间 provenance 探针无法执行（{exc}）——跑不动就等于没探，不放行"]
    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return [
            "命名空间 provenance 探针无有效输出（解释器自身被外来环境打崩）："
            + ((proc.stderr or proc.stdout or "")[-400:])
        ]
    issues: list[str] = []
    bases = [repo_root, Path(payload["prefix"]), Path(payload["base_prefix"])]
    for name, err in payload["errors"].items():
        issues.append(f"哨兵 import 失败（外来环境遮蔽/二进制不兼容）：{name} → {err}")
    for name, file in payload["modules"].items():
        if file and any(_path_under(file, b) for b in bases):
            continue
        issues.append(
            f"命名空间遮蔽：`{name}` 解析到 {file or '<无 __file__>'}，"
            "不在本仓或活动解释器内——宿主 PYTHONPATH 渗入，清 PYTHONPATH 后复跑门禁"
        )
    return issues


def suite_gate_issues(
    repo_root: Path,
    *,
    timeout: int = SUITE_TIMEOUT_S,
    real_home: str | None = None,
    python: str | None = None,
) -> list[str]:
    """全量测试 + 宿主数据目录绊线：红主干、或被测进程连真实库都必须拦下上传。

    口径由 `suite_pytest_command()` 给出，与 `.github/workflows/tests.yml` 一致：
    含 `--cov=lantai --cov-report=term-missing`。票 05 实测病灶：门禁此前不带覆盖率
    插桩，而 CI 带——`test_hook_timeout_budget.py` 的零余量墙钟断言在 CI 判红、在门禁
    判绿，红了三周没人知道。插桩只增加耗时不减少工作，因此这一条是更严的口径。
    `-o addopts=` 只是把口径钉死（本仓 pyproject 目前没有 addopts），防止将来有人
    往 addopts 里塞参数而悄悄改变门禁含义。挂 `tests/support/real_home_tripwire.py`
    挂成插件（按**连接路径**判定，不按 mtime——开发机上常驻服务自己也在写库）。
    退出码 0 之外还要求「至少一条真实通过的用例」，并把计数打进日志：全项目只剩
    skip 时退出码也是 0，旧判据会把它读成「全量测试通过」（票 05 续7 实测漏洞）。
    """
    py = python or sys.executable
    home = host_home_for_watch(repo_root) if real_home is None else real_home
    watch = os.path.join(home, "remembrance.db") if home else ""
    support = REPO_ROOT / "tests" / "support"
    # 门禁自己造的临时产物（绊线报告 + 覆盖率数据文件）必须放在一个门禁独占的临时目录
    # 里、退出即销毁。旧写法用 NamedTemporaryFile 造报告路径：with 一退出就把文件删了，
    # 子进程 pytest_configure 再以写模式把同名文件重建出来，父进程从此不管——每跑
    # 一次门禁就在 %TEMP% 留一个文件（票 05 续10 实测已积到 200 个）。COVERAGE_FILE 不
    # 指走则把 .coverage 落在被测仓库工作区。两条都是「门禁污染文件状态」，一并堵住。
    with tempfile.TemporaryDirectory(prefix="lantai_gate_suite_") as work_dir:
        report_path = os.path.join(work_dir, "tripwire_report.txt")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(support), env.get("PYTHONPATH", "")]).strip(
            os.pathsep
        )
        env["LANTAI_GATE_REAL_HOME"] = watch
        env["LANTAI_GATE_REPORT"] = report_path
        env["COVERAGE_FILE"] = os.path.join(work_dir, "coverage.dat")
        env.setdefault("API_KEY", _PROBE_KEY)
        cmd = suite_pytest_command(py=py)
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

        counts = suite_counts(output)
        print(
            "[....] 全量测试计数: "
            + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "解析不到收尾行（空跑）")
        )
        issues: list[str] = []
        if exit_code != 0:
            summary = next(
                (
                    ln
                    for ln in reversed(output.splitlines())
                    if re.search(r"\d+ (failed|error)", ln)
                ),
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
        else:
            summary_issue = suite_summary_issue(output)
            if summary_issue:
                issues.append(summary_issue)

        issues.extend(
            tripwire_report_issues(report_text, home=home or "(宿主目录未指明)", watch=watch)
        )
        return issues


def mcp_exposure_issue(active: int, total: int) -> str | None:
    """默认暴露 == 全量注册表 => 阻断。结构性判据，不含可调数字。

    票 05 续31 起生产语义已改：`lantai/cli/mcp.py::_get_active_tools()` 默认档
    fail-closed 到 `HERMES_CORE_TOOLS` 核心集，未知取值同样落核心集且可观测告警，
    全量注册表只有显式 `LANTAI_MCP_PROFILE=full` 才给。本判据保留是钉这个不变量：
    谁把默认档改回 `return TOOLS`、或悄悄加第二条放宽到全量的取值，这里红。
    提示文本仍只准开「实测真能兑现」的处方——双向对账由
    `test_mcp_fail_message_only_advertises_remedies_that_really_narrow` 钉住
    （提示里点名的取值集合 == monkeypatch 真调生产测得的集合，续21 教训）。
    """
    if total and active >= total:
        return (
            f"默认 MCP 暴露未收窄：不设 profile 时 tools/list 返回全部 {active}/{total} 个工具"
            "——阻断发布。续31 已把默认档改成 fail-closed 白名单：不设/空/未知取值 ⇒ "
            "`HERMES_CORE_TOOLS` 核心集（未知取值额外告警，不静默），只有显式声明 full "
            "才允许全量（评审指定的开关名 `LANTAI_MCP_EXPOSE` 不存在，显式开关落在 "
            "`LANTAI_MCP_PROFILE`/`MCP_TOOL_PROFILE`）。本项只看默认档：门禁探针会剥掉 "
            "profile 环境变量，单在部署侧设变量不会让它变绿。这里报全量说明默认档被改回"
            "`return TOOLS`（或新增第二条放宽路径）。放行需要维护者显式签字，并把口径"
            "同步进 docs/release-process.md「MCP 默认暴露」。"
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


# ---------------------------------------------------------------------------
# 老库升级回放（票 06：补齐门禁的 legacy 盲区）
#
# 既有「迁移与列约束漂移」项只比对 create_all 建出来的全新库，结构上看不见
# 「老库缺列」——memorycandidate.shared_scope 正是这么躲过门禁的（票 05 续9）。
# 该项把基线换成「上一版真实 schema + 真实迁移链升级」，判据仍走唯一真源
# scripts/schema_parity.py，与全新库共用同一套比对口径，避免两套规则分叉。
# ---------------------------------------------------------------------------

LEGACY_SNAPSHOT_DIR = Path("tests") / "fixtures" / "legacy-schema"
_LEGACY_PROBE_SCRIPT = "scripts/legacy_parity_probe.py"
_LEGACY_MARKER = "LEGACYPAYLOAD"


def load_schema_parity():
    """载入 schema 判据唯一真源（按路径加载，不塞 sys.path、不写模块全局）。"""
    import importlib.util

    path = REPO_ROOT / "scripts" / "schema_parity.py"
    spec = importlib.util.spec_from_file_location("lantai_gate_schema_parity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入 schema 判据模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_parity_issues_from_payload(payload: dict) -> list[str]:
    """把老库回放回报翻成阻断项。纯函数：单测可直接喂变异样本，不必起子进程。"""
    parity = load_schema_parity()
    issues: list[str] = []
    for item in payload.get("probe_errors") or []:
        issues.append(f"老库回放探针自身异常（没取证不等于取到证）：{item}")
    anchors = payload.get("anchors") or []
    if not anchors:
        issues.append("没有任何老库 schema 快照锚点：该项形同虚设，不放行（票 06 门禁盲区）")
        return issues

    target = payload.get("target_schema_version")
    measured_nulls: set[str] = set()
    for anchor in anchors:
        tag = anchor.get("source_tag", "?")
        for item in anchor.get("migration_failures") or []:
            issues.append(f"[{tag}] 迁移失败（红着也不能发布）：{item}")
        for item in anchor.get("missing_tables") or []:
            issues.append(f"[{tag}] 老库升级后仍缺表：{item}")
        for item in anchor.get("absent_columns") or []:
            issues.append(f"[{tag}] 跑完整条迁移链仍缺列，老库上写入会直接崩：{item}")
        for item in anchor.get("pk_drift") or []:
            issues.append(f"[{tag}] 主键约束漂移：{item}")
        for item in anchor.get("index_drift") or []:
            issues.append(f"[{tag}] 模型声明 index=True 但老库没有索引：{item}")
        for item in parity.ungated_null_drift(anchor):
            issues.append(f"[{tag}] 不在书面豁免清单内的 NULL 约束漂移：{item}")
        end_version = anchor.get("end_version")
        if isinstance(target, int) and end_version != target:
            issues.append(
                f"[{tag}] 迁移链没跑到目标版本：end_version={end_version} target={target}"
            )
        measured_nulls.update(anchor.get("null_drift_keys") or [])

    for key in parity.null_drift_waivers_without_evidence(measured_nulls):
        issues.append(
            f"NULL 豁免清单里的 {key} 已无法由任何老库锚点复现——清单成了死账，"
            f"要么漂移真被修好了（删掉该条），要么锚点覆盖退化了"
        )
    if not payload.get("home_isolated"):
        issues.append("老库回放探针没有跑在临时 LANTAI_HOME 里——它自己就可能污染生产，不放行")
    return issues


def legacy_parity_probe(repo_root: Path, *, python: str | None = None, timeout: int = 300) -> dict:
    """临时 LANTAI_HOME 里子进程跑老库回放，回报各锚点的结构性漂移。"""
    py = python or sys.executable
    probe: dict = {
        "anchors": [],
        "probe_errors": [],
        "home_isolated": False,
        "target_schema_version": None,
    }
    snap_dir = repo_root / LEGACY_SNAPSHOT_DIR
    host_home = host_lantai_home(repo_root) or "__none__"
    with tempfile.TemporaryDirectory(prefix="lantai_gate_legacy_") as probe_home:
        env = isolated_probe_env(probe_home)
        env["LANTAI_GATE_HOST_HOME"] = host_home
        script = repo_root / _LEGACY_PROBE_SCRIPT
        try:
            proc = subprocess.run(  # noqa: S603
                [py, "-u", str(script), str(snap_dir)],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe["probe_errors"] = [f"探针无法执行：{exc}"]
            return probe
    line = next(
        (ln for ln in (proc.stdout or "").splitlines() if ln.startswith(_LEGACY_MARKER)), ""
    )
    if not line:
        probe["probe_errors"] = [
            f"探针没有回报（exit={proc.returncode}）：" + (proc.stderr or "")[-400:]
        ]
        return probe
    try:
        payload = json.loads(line[len(_LEGACY_MARKER) :])
    except ValueError as exc:
        probe["probe_errors"] = [f"回报不可解析：{exc}"]
        return probe
    payload["home_isolated"] = os.path.normcase(
        str(payload.get("home_used", ""))
    ) != os.path.normcase(host_home) and bool(payload.get("home_used"))
    for key, value in probe.items():
        payload.setdefault(key, value)
    return payload


def legacy_parity_issues(repo_root: Path) -> list[str]:
    return legacy_parity_issues_from_payload(legacy_parity_probe(repo_root))


# ---------------------------------------------------------------------------
# CI 口径一致性（票 05）
#
# 发布门禁此前只跑自己那套命令，与 CI 的步骤并不等价：少了覆盖率插桩、少了
# 遗忘质量离线门禁。于是「门禁绿」根本推不出「CI 绿」。这里做两件事：
#   1. 门禁的测试命令与 CI 完全一致（含 --cov）；
#   2. 结构核对：CI 里每一条检查命令都必须被某个门禁项认领，认领的命令也必须
#      真的还存在于 CI 里——两边都对不上就判红，防止盲区再次长出。
# ---------------------------------------------------------------------------

SUITE_COV_ARGS = ["--cov=lantai", "--cov-report=term-missing"]
FORGETTING_SCRIPT = "scripts/run_forgetting_quality.py"
FORGETTING_PASS_MARKER = "门禁结果: PASS"
_RUN_RE = re.compile(r"^(?:-\s+)?run:\s*(.*)$")
_BLOCK_CHARS = {"|", ">", "|-", ">-", "|+", ">+"}
# 非判据命令的**书面**豁免清单：CI 里每条 run 命令都要么被认领、要么在这里写明理由。
# 检查类命令（pytest / ruff / python scripts/...）不许出现在这里，有测试盯着。
CI_STEP_EXEMPTIONS: dict[str, str] = {
    "pip install -e .[dev]": "环境准备：安装本包与 dev extra，不是判据；"
    "装不上会在「构建产物」与「全量测试」两项里暴露",
    "pip install build": "取构建前端；发布门禁自己用 uv / python -m build 直接出产物取证",
    'echo "VERSION=${GITHUB_REF#refs/tags/}" >> $GITHUB_OUTPUT': "Actions 输出变量赋值，不是检查",
    'echo "OWNER=${GITHUB_REPOSITORY_OWNER,,}" >> $GITHUB_OUTPUT': "Actions 输出变量赋值，不是检查",
}

# CI 检查步骤 -> 认领它的门禁项。键必须与 CI 里解析出的命令逐字一致。
CI_STEP_OWNERS: dict[str, str] = {
    "ruff check lantai/ tests/ scripts/": "CI lint 门禁",
    "ruff format --check lantai/ tests/ scripts/": "CI lint 门禁",
    "pytest tests/ -q": "全量测试 + 宿主目录绊线",
    "pytest tests/ -q --cov=lantai --cov-report=term-missing": "全量测试 + 宿主目录绊线",
    "python scripts/run_forgetting_quality.py --check": "遗忘质量离线门禁",
    "python -m build --wheel": "构建产物（wheel）",
}


def suite_pytest_command(*, py: str | None = None) -> list[str]:
    """门禁的全量测试命令，口径 = tests.yml 的 CI 步骤（含覆盖率插桩）。"""
    return [
        py or sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        *SUITE_COV_ARGS,
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-p",
        _TRIPWIRE_PLUGIN,
    ]


def normalize_run_commands(text: str) -> list[str]:
    """把 run 块正文拆成一条条命令：去空行、去注释、压缩多余空白。"""
    out: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def iter_run_bodies(text: str):
    """从 workflow 文本里抽出每个 `run:` 的正文（标量与 | > 块两种写法）。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        stripped = raw_line.strip()
        m = _RUN_RE.match(stripped)
        if not m:
            i += 1
            continue
        inline = m.group(1).strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        i += 1
        if inline in _BLOCK_CHARS:
            block = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    block.append("")
                    i += 1
                    continue
                if (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                block.append(nxt)
                i += 1
            yield "\n".join(block)
        else:
            yield inline


def ci_run_commands(repo_root: Path) -> list[str]:
    cmds: list[str] = []
    wf_dir = Path(repo_root) / ".github" / "workflows"
    if not wf_dir.is_dir():
        return cmds
    for path in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        for body_text in iter_run_bodies(path.read_text(encoding="utf-8", errors="replace")):
            cmds.extend(normalize_run_commands(body_text))
    return cmds


def ci_step_coverage_issues(repo_root: Path) -> list[str]:
    """fail-closed 对账：CI 里每一条 run 命令都必须「被认领」或「被书面豁免」。

    续10 的判据只把命中 `CI_CHECK_PREFIXES` 的命令当检查项——那张前缀表是 fail-open 的：
    `python -m build --wheel`、`python -m mypy`、任何换个写法的检查步骤都不在表里，于是
    既不需要认领、也不会有人发现，门禁绿而 CI 红。v0.66 的构建维度正是这样滑出去的。
    现在反过来：默认全部要对账；认领清单里的命令若已从 CI 消失，同样判红（死账）。
    """
    cmds = list(dict.fromkeys(ci_run_commands(repo_root)))
    issues = [
        f"CI 步骤 `{cmd}` 既没有被发布门禁项认领，也没有写进豁免清单——"
        "门禁绿推不出 CI 绿（票 05 续12：前缀白名单是 fail-open 的）"
        for cmd in cmds
        if cmd not in CI_STEP_OWNERS and cmd not in CI_STEP_EXEMPTIONS
    ]
    seen = set(cmds)
    issues += [
        f"发布门禁声称覆盖 `{owned}`，但 CI 里已无此命令——认领清单成了死账，删掉它或恢复步骤"
        for owned in CI_STEP_OWNERS
        if owned not in seen
    ]
    if not cmds:
        issues.append("没从 .github/workflows 解析到任何 run 命令：核对形同虚设，不放行")
    return issues


def ci_test_steps(repo_root: Path) -> list[str]:
    """CI 里真正跑测试的那些步骤（`python -m pytest` 归一到 `pytest`）。"""
    out: list[str] = []
    for cmd in ci_run_commands(repo_root):
        if cmd.startswith("python -m pytest "):
            cmd = "pytest " + cmd[len("python -m pytest ") :]
        if cmd.startswith("pytest "):
            out.append(cmd)
    return out


def gate_suite_flag_issues(repo_root: Path) -> list[str]:
    """门禁的 pytest 命令必须是 CI 测试步骤的**超集**。

    续10 抓的是「门禁少跑一步 CI」；反方向同样致命：门禁自己少带一个旗标（`-W`、
    `--cov`、`-p`……）就等于换了口径，「门禁绿」再也推不出「CI 绿」。门禁可以额外加
    隔离用的旗标（绊线插件、`no:cacheprovider`、`-o addopts=`），但不许少带 CI 带的。
    """
    gate = suite_pytest_command()
    issues: list[str] = []
    for step in ci_test_steps(repo_root):
        tokens = [t for t in step.split()[1:] if t != "pytest"]
        missing = [t for t in tokens if t not in gate]
        if missing:
            issues.append(
                f"门禁的 pytest 命令缺少 CI 步骤 `{step}` 的项 {missing}——"
                "门禁口径必须包含 CI 口径，否则门禁绿推不出 CI 绿"
            )
    return issues


def addopts_fidelity_issues(repo_root: Path) -> list[str]:
    """门禁把 `addopts` 钉成空，CI 不钉：一旦 pyproject 真声明了 addopts，两边跑的就不是同一件事。

    钉子本身是有意为之（防止有人往 addopts 塞参数而悄悄改门禁含义），但它必须**可核对**：
    声明一旦非空，门禁必须判红逼人显式对齐，而不是让钉子悄悄生效、让门禁系统性比 CI 宽松。

    票 05 续19 D4：这条检查自己也有两个「没取证就当取到证」的出口——钉子从门禁命令里
    消失、或 pyproject 读不到时，原先都 `return []`，于是「把核对拆掉」这件事本身永远绿。
    两处都改成显式判红：核对的前提不成立就是未取证，未取证就不放行。
    """
    if "addopts=" not in suite_pytest_command():
        return [
            "门禁的 pytest 命令不再钉 `-o addopts=`：addopts 一致性核对失去依据，这项检查"
            "成了恒空的摆设（票 05 续19 D4，与「缺 ruff 记成通过」同族）——"
            "要么把钉子加回门禁命令，要么把两边口径写进 CI 与门禁共同的命令定义"
        ]
    path = Path(repo_root) / "pyproject.toml"
    if not path.is_file():
        return [f"找不到 pyproject.toml（{path}），无法核对 addopts：依据缺失 = 未取证，不放行"]
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 运行环境 < 3.11
        return ["无法读取 pyproject 的 addopts（缺 tomllib）——没取证不等于取到证，不放行"]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"pyproject.toml 解析失败，无法核对 addopts：{exc}"]
    addopts = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts")
    if addopts:
        return [
            f"pyproject 声明了 addopts={addopts!r}，而门禁用 `-o addopts=` 把它强制清空，"
            "CI 却按声明执行——两边口径不等价，请把差异显式对齐（改 CI 命令或去掉 addopts）"
        ]
    return []


def forgetting_gate_issues(returncode: int, output: str) -> list[str]:
    """纯判据：退出码 0 且必须回报结论标记，才算取到证。"""
    if returncode != 0:
        tail = "\n".join([ln for ln in (output or "").splitlines() if ln.strip()][-8:])
        return [f"遗忘质量离线门禁失败（exit={returncode}）——CI 的这一步红则不可发布：\n{tail}"]
    if FORGETTING_PASS_MARKER not in (output or ""):
        return [
            f"遗忘质量门禁退出码 0 但没有回报「{FORGETTING_PASS_MARKER}」：空跑/没取证不等于取到证"
        ]
    return []


def run_forgetting_gate(
    repo_root: Path, *, python: str | None = None, timeout: int = 900
) -> list[str]:
    """临时 LANTAI_HOME 里真跑一次 CI 的遗忘质量门禁步骤。"""
    py = python or sys.executable
    with tempfile.TemporaryDirectory(prefix="lantai_gate_forget_") as probe_home:
        env = isolated_probe_env(probe_home)
        env["LANTAI_GATE_HOST_HOME"] = host_lantai_home(repo_root) or "__none__"
        try:
            proc = subprocess.run(  # noqa: S603
                [py, "-u", str(repo_root / FORGETTING_SCRIPT), "--check"],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"遗忘质量门禁探针无法执行：{exc}"]
    return forgetting_gate_issues(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def wheel_metadata_issues(wheel: str | Path, *, expected_version: str) -> list[str]:
    """只读核对构建产物：版本必须等于发布目标，包里必须真有代码和入口。

    「版本一致性」只看仓库里的文本；产物里带的什么版本、打包了没有，此前没人管。
    """
    path = Path(wheel)
    if not path.is_file():
        return [f"构建产物不存在：{path}"]
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"构建产物不是合法 wheel/zip（{path.name}）：{exc}"]
    with zf:
        names = zf.namelist()
        metas = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metas) != 1:
            return [f"wheel 里 .dist-info/METADATA 数量异常：{len(metas)}（期望 1）"]
        found = ""
        for line in zf.read(metas[0]).decode("utf-8", "replace").splitlines():
            if line.startswith("Version:"):
                found = line.split(":", 1)[1].strip()
                break
        issues: list[str] = []
        if found != expected_version:
            issues.append(
                f"wheel 的 METADATA Version={found!r} 与发布目标 {expected_version!r} 不一致"
                "——发出去的产物和声称的版本不是同一个"
            )
        if not [n for n in names if n.startswith("lantai/")]:
            issues.append("wheel 里没有任何 lantai/ 模块：构建出来的不是这个包")
        eps = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        if not eps:
            issues.append("wheel 缺少 entry_points.txt：lantai-mcp / lantai-server 不会注册")
        else:
            text = zf.read(eps[0]).decode("utf-8", "replace")
            for script in ("lantai-mcp", "lantai-server"):
                if script not in text:
                    issues.append(f"wheel 的 console_scripts 缺少 {script}")
    return issues


def build_wheel_commands(
    repo_root: Path, outdir: str, *, python: str | None = None
) -> list[list[str]]:
    """离线可用的构建前端候选。"""
    py = python or sys.executable
    cmds: list[list[str]] = []
    uv = shutil.which("uv")
    if uv:
        cmds.append([uv, "build", "--offline", "--wheel", "--out-dir", str(outdir)])
    cmds.append([py, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(outdir)])
    return cmds


def run_build_gate(
    repo_root: Path, *, expected_version: str, python: str | None = None, timeout: int = 600
) -> tuple[list[str], str | None]:
    """真构建一次 wheel 并核对产物；跑不出产物就判红，不给静默跳过留口子。

    返回 (issues, wheel_path)。CI 的发布链路（ci.yml）是 `pip install build` +
    `python -m build --wheel` 再进 Docker；门禁不在这里复现 Docker（要 registry 凭据与
    网络，属人工闸门），但至少必须证明**进镜像的那个 wheel 能构建、且版本对得上**。
    """
    with tempfile.TemporaryDirectory(prefix="lantai_gate_build_") as outdir:
        problems: list[str] = []
        for cmd in build_wheel_commands(repo_root, outdir, python=python):
            try:
                proc = subprocess.run(  # noqa: S603
                    cmd,
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(f"{cmd[0]}: {exc}")
                continue
            wheels = sorted(Path(outdir).glob("*.whl"))
            if proc.returncode == 0 and wheels:
                return wheel_metadata_issues(wheels[0], expected_version=expected_version), str(
                    wheels[0]
                )
            tail = " | ".join(
                [
                    ln
                    for ln in ((proc.stdout or "") + (proc.stderr or "")).splitlines()
                    if ln.strip()
                ][-2:]
            )
            problems.append(f"{cmd[0]} rc={proc.returncode}: {tail[:240]}")
        wheels = sorted(Path(outdir).glob("*.whl"))
        if wheels:
            return wheel_metadata_issues(wheels[0], expected_version=expected_version), str(
                wheels[0]
            )
    return (
        [
            f"构建维度未取证：没有可用的离线构建前端，也没产出 wheel——没取证不等于取到证，不放行。"
            f"尝试过：{'; '.join(problems) or '（无候选命令）'}"
        ],
        None,
    )


def main(argv: list[str] | None = None) -> int:
    # 每次运行一本账（票 05 续19 D2）：results 是模块全局，入口不清空就会把上一轮的
    # 判定重复计入分母——同进程连跑两次实测第二次报「16/19 通过, 2 失败」，通过率、
    # 失败条数全是编的，门禁不再可复现。announce_skip() 早已改用调用方局部 list，
    # 这里同口径把旧账归零（保留全局只为不动 ~25 处 check() 调用面）。
    results.clear()

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
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(parsed_argv)
    # 诊断四要素里的「失败命令」：逐项最小复现写在 FAIL 诊断行，这里补上本次门禁
    # 到底是带着哪些旗标跑的（旗标合成绿灯是老事故，见模块 docstring）。
    print("门禁命令：python scripts/release_check.py " + " ".join(parsed_argv))

    # 未取证账本必须在 lint 自查之前就存在（票 05 续19 D1）：晚创建一项就把那一项
    # 漏在账外，漏账的 [SKIP] 会被下面的 PASS 分支读成「已执行且通过」。
    skips: list[str] = []

    # lint 自查放在最前（票 21）：它红 = CI 的全量测试一步不跑，
    # 这时再查版本/tag 没有意义——先让人看见门禁本身是红的。
    lint_failures = lint_gate_issues(REPO_ROOT, skips=skips)
    for issue in lint_failures:
        check("CI lint 门禁", False, issue)
    if not lint_failures and "CI lint 门禁" not in skips:
        check("CI lint 门禁", True, "ruff check + ruff format --check 均通过")

    fidelity_failures = (
        ci_step_coverage_issues(REPO_ROOT)
        + gate_suite_flag_issues(REPO_ROOT)
        + addopts_fidelity_issues(REPO_ROOT)
    )
    for issue in fidelity_failures:
        check("CI 步骤覆盖核对", False, issue)
    if not fidelity_failures:
        check(
            "CI 步骤覆盖核对",
            True,
            f"{len(CI_STEP_OWNERS)} 条 CI 检查步骤都有门禁项认领、无失配死账，"
            "且门禁 pytest 口径包含 CI 口径、addopts 钉子仍惰性",
        )

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

    build_failures, build_wheel = run_build_gate(REPO_ROOT, expected_version=version)
    for issue in build_failures:
        check("构建产物（wheel）", False, issue)
    if not build_failures:
        check(
            "构建产物（wheel）",
            True,
            f"{Path(build_wheel).name} 离线构建成功，METADATA 版本 = v{version}，"
            "含 lantai/ 载荷与 lantai-mcp / lantai-server 入口",
        )

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

    provenance_failures = namespace_provenance_issues(REPO_ROOT)
    for issue in provenance_failures:
        check("命名空间 provenance", False, issue)
    if not provenance_failures:
        check(
            "命名空间 provenance",
            True,
            "第一方（lantai/tests）与三方哨兵（pydantic/sqlmodel/fastapi）全部解析到本仓"
            "或活动解释器内——宿主 PYTHONPATH 未渗入门禁会话（票 04）",
        )

    schema_failures = schema_drift_issues(REPO_ROOT)
    for issue in schema_failures:
        check("迁移与列约束漂移", False, issue)
    if not schema_failures:
        check(
            "迁移与列约束漂移",
            True,
            "临时 LANTAI_HOME 内 init_db() 全链通过，metadata 与实表列/NULL 约束一致",
        )

    legacy_payload = legacy_parity_probe(REPO_ROOT)
    legacy_failures = legacy_parity_issues_from_payload(legacy_payload)
    for issue in legacy_failures:
        check("老库升级回放与列对齐", False, issue)
    if not legacy_failures:
        tags = " / ".join(str(a.get("source_tag")) for a in legacy_payload["anchors"])
        check(
            "老库升级回放与列对齐",
            True,
            f"{len(legacy_payload['anchors'])} 个老库锚点（{tags}）"
            "经真实迁移链升到目标版本后与模型对齐（缺列=0、索引齐、NULL 漂移仅在书面清单内）",
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
                "pytest tests/ --cov=lantai 退出码 0（与 CI 同口径），且全程未连接宿主真实 LANTAI_HOME",
            )

    forgetting_failures = run_forgetting_gate(REPO_ROOT)
    for issue in forgetting_failures:
        check("遗忘质量离线门禁", False, issue)
    if not forgetting_failures:
        check(
            "遗忘质量离线门禁",
            True,
            "scripts/run_forgetting_quality.py --check 通过（CI tests.yml 的同一条步骤）",
        )

    print("=" * 60)
    line, code = summarize(results, n_skip=len(skips))
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
