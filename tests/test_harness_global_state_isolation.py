"""装配期全局状态隔离回归（票 05 续14，验收底线 G2：单测不得污染生产全局）。

判据分四层，缺一层就是空转：

1. **静态扫描（AST）**：`tests/` 内除唯一写入器 `tests/support/harness_state.py` 之外，
   不得出现裸的 `os.environ` 改写、`settings.X = ...` 单例改写、`sys.path` 改写。
   这是 G2 的长期锁：将来谁再写一行 `os.environ[...] = ...`，本用例立刻红。
2. **进程内事实**：装配策略确实生效——LLM 出口被**强制**成不可路由的黑洞端点，
   而不是 `setdefault`（宿主配了真 key 时 setdefault 静默放行 = 真凭据直接打公网）。
3. **真实子会话**：用模拟的宿主环境（真 key + 公网端点）起一个真实 pytest 子会话，
   证明子会话里 ambient 凭据被覆盖、会话退出时 ambient 原值被精确复原、装配期新增的
   键被删除、`settings` 单例回到出厂默认。复原挂在 `pytest_unconfigure` 上，只有真实
   会话退出才跑得通这条判据。
4. **负向对照**：扫描器必须抓出人为植入的几类污染，且不误报合规写法——防止「什么都
   扫不到」被当成绿灯。
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lantai.core.settings import Settings, settings
from lantai.ingestion.safety import validate_api_url
from tests.support.harness_state import (
    AUDIT_ENV_KEY,
    HARNESS_ENV_POLICY,
    HARNESS_FEATURE_FLAG_POLICY,
    HARNESS_LLM_BLACKHOLE_URL,
    HARNESS_LLM_FAKE_KEY,
    env_context,
    registry_view,
    set_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
SANCTIONED_WRITER = TESTS_DIR / "support" / "harness_state.py"

# 模拟「宿主已配真凭据」——正是 setdefault 写法会漏掉的那一类环境
FAKE_AMBIENT_KEY = "sk-AMBIENT-SECRET-DO-NOT-LEAK"
FAKE_AMBIENT_BASE_URL = "https://api.openai.com/v1"

ENV_MUTATORS = ("setdefault", "pop", "update", "clear", "popitem")


def _fp(value: str) -> str:
    """与 harness_state.mask 同算法的凭据指纹（快照不落明文）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_environ_expr(node: ast.AST | None, aliases=frozenset()) -> bool:
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr == "environ"
    return isinstance(node, ast.Name) and node.id in aliases


def _environ_aliases(tree: ast.AST) -> set[str]:
    """收集 `env = os.environ` 形态的别名（`dict(os.environ)`/`.copy()` 是拷贝，不算）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_environ_expr(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _is_syspath_expr(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "path"
    )


def _settings_target(node: ast.AST | None) -> str | None:
    """识别 `settings.X = ...` 与 `mod.settings.X = ...`（生产配置单例的原地改写）。"""
    if not isinstance(node, ast.Attribute):
        return None
    base = node.value
    if isinstance(base, ast.Name) and base.id == "settings":
        return node.attr
    if isinstance(base, ast.Attribute) and base.attr == "settings":
        return node.attr
    return None


def _parent_map(tree: ast.AST) -> dict:
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing(node: ast.AST, parents: dict) -> str:
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return str(cur.name)
        if isinstance(cur, ast.ClassDef):
            return f"class {cur.name}"
        cur = parents.get(cur)
    return "<module>"


def audit_source(path: Path) -> list[str]:
    """扫一个源文件里的「装配期全局污染」写法，返回违规条目（空 = 干净）。"""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # 解析不了就判红：不许给扫描器留静默盲区
        return [f"{_rel(path)}: 无法解析（{exc.msg}），污染扫描拒绝放行"]

    aliases = _environ_aliases(tree)
    parents = _parent_map(tree)
    guarded = path.resolve() == SANCTIONED_WRITER.resolve()
    findings: list[str] = []

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None:  # ast.Module/arguments 等无行号节点不是违规载体
            continue
        where = f"{_rel(path)}:{lineno} ({_enclosing(node, parents)})"

        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_environ_expr(target.value, aliases):
                    findings.append(f"{where} ENV_WRITE 裸写 os.environ：{ast.unparse(target)}")
                base = target.value if isinstance(target, (ast.Attribute, ast.Subscript)) else None
                field = _settings_target(target if isinstance(target, ast.Attribute) else base)
                if field:
                    findings.append(f"{where} SETTINGS_WRITE 原地改配置单例：{field}")

        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_environ_expr(target.value, aliases):
                    findings.append(f"{where} ENV_WRITE 裸删 os.environ：del {ast.unparse(target)}")

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and _is_environ_expr(func.value, aliases):
                if func.attr in ENV_MUTATORS:
                    findings.append(f"{where} ENV_WRITE os.environ.{func.attr}(...)")
            elif isinstance(func, ast.Attribute) and _is_syspath_expr(func.value):
                if func.attr in ("insert", "append", "extend", "remove"):
                    findings.append(f"{where} SYSPATH_WRITE sys.path.{func.attr}(...)")

    if guarded:
        # 登记式写入器内 ENV_WRITE / SYSPATH_WRITE 均为已登记形态：ENV 写走
        # 登记复原，SYSPATH 清除走台账（registry_view()["syspath_removals"]，
        # 票 04 遮蔽防御搬入唯一写入器）。其余文件两者都仍然一律判红。
        findings = [f for f in findings if "ENV_WRITE" not in f and "SYSPATH_WRITE" not in f]
    return findings


def _all_test_sources() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_harness_state_writer_exists():
    """唯一写入器必须存在——删掉它等于把 G2 的锁拆了，不允许。"""
    assert SANCTIONED_WRITER.is_file(), f"唯一登记式写入器缺失：{_rel(SANCTIONED_WRITER)}"


def test_tests_tree_has_no_unguarded_global_writes():
    """G2 主判据：tests/ 内除登记式写入器外，零裸写全局。"""
    sources = _all_test_sources()
    assert len(sources) > 100, f"扫描范围异常（只扫到 {len(sources)} 个文件）：扫描器在空转"
    violations: list[str] = []
    for path in sources:
        violations.extend(audit_source(path))
    assert not violations, "装配期全局污染（未登记/未复原）：\n" + "\n".join(violations)


def test_llm_exit_is_forced_to_non_routable_blackhole():
    """进程内事实：LLM 出口不取决于宿主环境，被强制成不可路由的黑洞端点。"""
    for name, expected in HARNESS_ENV_POLICY.items():
        assert os.environ.get(name) == expected, (
            f"{name} 未被装配策略强制覆盖（宿主值原样渗进测试进程）：{os.environ.get(name)!r}"
        )
    assert settings.OPENAI_BASE_URL == HARNESS_LLM_BLACKHOLE_URL
    assert settings.OPENAI_API_KEY == HARNESS_LLM_FAKE_KEY


def test_registry_records_every_harness_global_write():
    """装配期写过的全局项必须全部登记在案（否则会话结束无从复原）。"""
    view = registry_view()
    missing_env = [k for k in HARNESS_ENV_POLICY if k not in view["env_writes"]]
    missing_flags = [k for k in HARNESS_FEATURE_FLAG_POLICY if k not in view["settings_writes"]]
    assert not missing_env, f"环境变量未登记：{missing_env}"
    assert not missing_flags, f"配置单例字段未登记：{missing_flags}"


def test_production_guard_rejects_harness_endpoint_but_accepted_the_old_one():
    """不 mock：直调真实 allowlist 校验（审计 M7）。

    黑洞端点被拒 ⇒ 即便某个 LLM 绑定逃过替身，后果也是「断网」而不是「外传」；
    旧装配值（`https://api.openai.com/v1`）**能通过**同一道校验 ⇒ 旧写法确实把
    真凭据送到了可路由的公网端点——G2 判「违反」不是修辞。
    """
    with pytest.raises(ValueError) as exc:
        validate_api_url(HARNESS_LLM_BLACKHOLE_URL)
    assert "https" in str(exc.value)
    assert validate_api_url(FAKE_AMBIENT_BASE_URL) == FAKE_AMBIENT_BASE_URL


def test_env_context_restores_previous_value_exactly():
    """`env_context` 必须写回原值；原本没有该键才删除（不许吞掉用户配置）。"""
    name = "LANTAI_HARNESS_ENVCONTEXT_PROBE"
    set_env(name, "ambient-original")
    before = os.environ.get(name)
    with env_context(name, "temp"):
        assert os.environ[name] == "temp"
    assert os.environ.get(name) == before

    absent = "LANTAI_HARNESS_ENVCONTEXT_ABSENT"
    assert absent not in os.environ
    with env_context(absent, "temp"):
        assert os.environ[absent] == "temp"
    assert absent not in os.environ


@pytest.fixture(scope="module")
def harness_session_report(tmp_path_factory) -> dict:
    """真实子会话：模拟宿主已配真凭据，跑一次 pytest 会话，读它退出时落下的审计。"""
    tmp = tmp_path_factory.mktemp("harness_session")
    audit_path = tmp / "audit.json"
    child_home = tmp / "home"
    child_home.mkdir()

    env = dict(os.environ)
    env["OPENAI_API_KEY"] = FAKE_AMBIENT_KEY
    env["OPENAI_BASE_URL"] = FAKE_AMBIENT_BASE_URL
    env["LANTAI_HOME"] = str(child_home)
    env["REMEMBRANCE_HOME"] = str(child_home)
    env[AUDIT_ENV_KEY] = str(audit_path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("VIRTUAL_ENV", None)
    env.pop("LANTAI_GATE_REPORT", None)
    env.pop("LANTAI_GATE_REAL_HOME", None)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(Path(__file__)),
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, (
        f"子会话退出码 {proc.returncode}：\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"
    )
    assert audit_path.is_file(), "子会话退出时没落审计：复原没有挂到会话生命周期上"
    return json.loads(audit_path.read_text(encoding="utf-8"))


def test_ambient_credentials_are_overridden_then_restored_on_session_exit(harness_session_report):
    """会话退出复原的端到端证据：覆盖发生在装配期，复原发生在 unconfigure。"""
    report = harness_session_report["report"]
    env = report["env"]
    assert "OPENAI_API_KEY" in harness_session_report["env_at_import_names"], (
        "子会话启动时宿主根本没有 ambient key：模拟环境没搭起来"
    )
    assert {"OPENAI_API_KEY", "OPENAI_BASE_URL"} <= set(env), "子会话未登记 LLM 出口写入"

    key = env["OPENAI_API_KEY"]
    assert key["before"]["fingerprint"] == _fp(FAKE_AMBIENT_KEY), (
        f"子会话的 ambient key 不是模拟值：{key['before']}"
    )
    assert key["at_teardown"]["fingerprint"] == _fp(HARNESS_LLM_FAKE_KEY), (
        "ambient 真凭据没被覆盖：setdefault 语义仍在放行宿主值"
    )
    assert key["after"]["fingerprint"] == _fp(FAKE_AMBIENT_KEY), "会话结束后 key 未复原为宿主原值"

    base = env["OPENAI_BASE_URL"]
    assert base["before"]["value"] == FAKE_AMBIENT_BASE_URL
    assert base["at_teardown"]["value"] == HARNESS_LLM_BLACKHOLE_URL
    assert base["after"]["value"] == FAKE_AMBIENT_BASE_URL, "base_url 未复原为宿主原值"

    assert report["env_residue"] == [], f"环境变量未精确复原：{report['env_residue']}"
    assert report["ambient_openai_key_restored"] is True
    assert report["ambient_openai_base_url_restored"] is True
    assert report["blackhole_url_gone_after_restore"] is True


def test_feature_flag_overrides_are_reverted_to_factory_defaults(harness_session_report):
    """装配期把 FEATURE_* 翻真是为了跑测试；出厂默认必须原样还回去。"""
    report = harness_session_report["report"]
    flags = report["settings"]
    for name in HARNESS_FEATURE_FLAG_POLICY:
        assert name in flags, f"{name} 未登记，会话结束无从复原"
        assert flags[name]["at_teardown"]["value"] == "True", (
            f"{name} 装配期没有被翻真：{flags[name]}"
        )
        assert flags[name]["exact"] is True, f"{name} 未复原：{flags[name]}"
    assert report["settings_residue"] == []
    for name, default in HARNESS_FEATURE_FLAG_POLICY.items():
        frozen = Settings.model_fields[name].default
        if frozen != default:  # 出厂 False 被装配期翻真的那几项才是关键证据
            assert flags[name]["before"]["value"] == str(frozen), (
                f"{name} 的复原基准不是出厂默认 {frozen!r}：{flags[name]['before']}"
            )
    assert Settings.model_fields["FEATURE_OBSIDIAN"].default is False, (
        "出厂默认已变：本用例的判据需要「默认关、装配开」这个差值"
    )


def test_audit_snapshot_never_carries_plaintext_credentials(harness_session_report):
    """隐私锁：审计文件里只许出现凭据指纹，不许出现明文。"""
    text = json.dumps(harness_session_report)
    assert FAKE_AMBIENT_KEY not in text, "审计快照把 ambient key 明文落盘了"
    assert _fp(FAKE_AMBIENT_KEY) in text, "审计快照里没有凭据指纹，无法核对复原一致性"


def test_scanner_detects_planted_pollution_and_spares_compliant_code(tmp_path):
    """负向对照：扫描器不是摆设——植入的污染必须全被抓到，合规写法不误报。"""
    bad = tmp_path / "bad_pollution.py"
    bad.write_text(
        "import os\nimport sys\n\n"
        'os.environ["X"] = "1"\n'
        'os.environ.setdefault("Y", "2")\n'
        'del os.environ["Z"]\n'
        "environ_alias = os.environ\n"
        'environ_alias["W"] = "3"\n'
        "sys.path.insert(0, 'scripts')\n"
        "settings.FEATURE_X = True\n",
        encoding="utf-8",
    )
    findings = audit_source(bad)
    text = " ".join(findings)
    for token in ("ENV_WRITE", "SYSPATH_WRITE", "SETTINGS_WRITE"):
        assert token in text, f"扫描器漏报 {token}：\n{text}"
    assert sum(1 for f in findings if "ENV_WRITE" in f) >= 4, (
        f"裸写 os.environ 只抓到 {findings}，别名/pop/del 中有漏网"
    )

    good = tmp_path / "good_compliant.py"
    good.write_text(
        "import os\n\n"
        "from tests.support.harness_state import env_context, set_env\n\n"
        "def uses_writer(monkeypatch):\n"
        '    set_env("A", "1")\n'
        '    monkeypatch.setenv("B", "2")\n'
        '    with env_context("C", "3"):\n'
        "        pass\n"
        "    snapshot = dict(os.environ)\n"
        '    value = os.environ.get("A")\n'
        "    return snapshot, value\n",
        encoding="utf-8",
    )
    assert audit_source(good) == [], f"合规写法被误报：{audit_source(good)}"
