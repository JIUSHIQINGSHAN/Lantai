"""tests/ 里每一处「不执行」都必须申报（票 05 续16）。

为什么需要这条锁：门禁 G1 的判据是「0 failed / 0 error / 0 skipped」，而 `skip` 本身
是一条**静默弃权**通道。续16 实测抓到一处真实掩盖：`tests/cognitive/test_cognitive_loop.py`
的 BL-03 `test_belief_persists` 在 `run_reflection()` 不产出 BELIEF candidate 时
`pytest.skip("没有产出 failure BELIEF candidate，跳过持久化测试")`——把晋升链路打挂后，
同一前提的 BL-02 判 `AssertionError`，BL-03 却只是 Skipped（复现：
`.scratch/gate/r16/repro_bl03_mask.py`）。环境缺工具/缺数据集的 skip 是诚实的，
**被测逻辑不产出**的 skip 是把红灯读成绿灯。

本文件把那次修复变成长期判据：
1. 申报清单逐条相等——新增、改名、改 reason、删掉不执行点都会判红（不是靠人记）；
2. 运行时 skip 的守卫条件必须只引用环境能力谓词（`exists()` / `which(` / `os.environ`
   / `settings.` / `getenv` / `importorskip`），凭「被测函数的返回值」弃权一律判红；
3. 扫描器不许空转：解析文件数与命中数都有下限断言，并用内联样本正反向自证。
"""

import ast
from collections import Counter
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent

# 环境能力谓词：缺了它们「不执行」是诚实申报，不是掩盖被测行为
ENV_PREDICATES = ("exists()", "which(", "os.environ", "getenv", "settings.", "importorskip")

SKIP_CALLS = {
    "pytest.skip": "pytest.skip",
    "pytest.xfail": "pytest.xfail",
    "pytest.importorskip": "pytest.importorskip",
    "unittest.skip": "unittest.skip",
    "unittest.skipIf": "unittest.skipIf",
    "unittest.skipUnless": "unittest.skipUnless",
}
MARK_CALLS = {
    "pytest.mark.skip": "pytest.mark.skip",
    "pytest.mark.skipif": "pytest.mark.skipif",
    "unittest.skip": "unittest.skip",
    "unittest.skipIf": "unittest.skipIf",
}

# 唯一合法的不执行申报面（续16 全仓实测 6 处 + 票 31 一处 + 票 32 三处）。
# BL-03 已改成断言，不在清单内。
DECLARED = Counter(
    {
        ("tests/test_longmemeval_core.py", "pytestmark.skipif", "数据集未下载（本地跑测专用）"): 1,
        ("tests/test_longmemeval_core.py", "pytest.skip", "预注册未生成"): 1,
        ("tests/test_mcp_principal_wiring.py", "pytest.skip", "AUTODREAM_ENABLED=false"): 2,
        (
            "tests/test_release_check.py",
            "pytest.skip",
            "本机无 git 可执行文件：release_check 的 git 用例无法真实执行",
        ): 1,
        ("tests/test_wheel_ships_ui.py", "pytest.mark.skipif", "需要 uv 构建 wheel"): 1,
        (
            "tests/test_digest_window_anchor.py",
            "pytest.mark.skipif",
            (
                "案 A 的触发-窗口组合行为需宿主时区 UTC+8（Asia/Shanghai 部署语义）才能"
                "忠实复现：非 +8 宿主上窗口锚定系统时区、语义不同——宁 skip 带由不脏写；"
                "配置契约与补跑锚定两臂宿主无关已覆盖其余面；非 +8 宿主如需强制实跑本臂"
                "（如 CI 验证 skip 逻辑），设 LANTAI_DIGEST_TZ_ARM_FORCE=1"
            ),
        ): 1,
        (
            "tests/test_readside_time_semantics.py",
            "pytest.mark.skipif",
            (
                "readside 本地日语义红测需宿主时区 ≠ UTC 才能复现 UTC 日错位：UTC 宿主上"
                "本地日与 UTC 日重合、aware 偏移为零，现状即正确（语义重合非假绿）——"
                "宁 skip 带由不脏写；强制实跑设 LANTAI_READSIDE_TZ_ARM_FORCE=1"
            ),
        ): 3,
    }
)
DECLARED_TOTAL = 10

# 无守卫的运行时弃权（能力探测循环走空后统一 skip）单独按申报集合管——它不是
# 「凭被测返回值弃权」，但同样必须逐条可查。新增一个这类点同样判红。
UNGUARDED_SKIP_KEYS = {
    (
        "tests/test_release_check.py",
        "pytest.skip",
        "本机无 git 可执行文件：release_check 的 git 用例无法真实执行",
    )
}


def _dotted(node) -> str:
    """把 `pytest.mark.skipif` / `shutil.which` 这类属性链拉成点分名。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _literal_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _reason(call, reason_index):
    pos = call.args[reason_index] if len(call.args) > reason_index else None
    got = _literal_str(pos) if pos is not None else None
    if got is not None:
        return got
    for kw in call.keywords or []:
        if kw.arg == "reason":
            got = _literal_str(kw.value)
            if got is not None:
                return got
    return "<dynamic>"


def _guard_source(call, src: str) -> str:
    """向上找最近的 If / 复合条件，返回其源码文本（用于判断弃权依据是环境还是被测行为）。"""
    node = call
    while getattr(node, "_parent", None) is not None:
        parent = node._parent
        if isinstance(parent, ast.If):
            return ast.get_source_segment(src, parent.test) or ""
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            break
        node = parent
    return ""


def _looks_environmental(text: str) -> bool:
    return any(tok in text for tok in ENV_PREDICATES)


def scan_source(rel: str, src: str) -> list[dict]:
    """解析单文件源码，返回所有「不执行」申报点。"""
    tree = ast.parse(src, filename=rel)
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    sites: list[dict] = []

    def add(kind: str, reason: str, guard: str, lineno: int, declared_by_env: bool):
        sites.append(
            {
                "file": rel,
                "kind": kind,
                "reason": reason,
                "guard": guard,
                "lineno": lineno,
                "environmental": declared_by_env or _looks_environmental(guard),
            }
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                name = _dotted(call.func if call is not None else dec)
                kind = MARK_CALLS.get(name)
                if kind is None:
                    continue
                reason = _reason(call, 1) if call is not None else "<no-reason>"
                guard = (
                    ast.get_source_segment(src, call.args[0]) or "" if call and call.args else ""
                )
                add(
                    kind,
                    reason,
                    guard,
                    getattr(dec, "lineno", node.lineno),
                    kind == "pytest.mark.skip",
                )
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "pytestmark" not in names:
                continue
            call = node.value if isinstance(node.value, ast.Call) else None
            name = _dotted(call.func if call is not None else node.value)
            if name not in MARK_CALLS:
                continue
            kind = "pytestmark." + name.rsplit(".", 1)[-1]
            reason = _reason(call, 1) if call is not None else "<no-reason>"
            guard = ast.get_source_segment(src, call.args[0]) or "" if call and call.args else ""
            add(kind, reason, guard, node.lineno, kind.endswith("skip"))
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            kind = SKIP_CALLS.get(name)
            if kind is None:
                continue
            reason = _reason(node, 0)
            add(kind, reason, _guard_source(node, src), node.lineno, name == "pytest.importorskip")
    return sites


def collect(root: Path = TESTS_ROOT) -> tuple[list[dict], int]:
    sites: list[dict] = []
    parsed = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        src = path.read_text(encoding="utf-8")
        parsed += 1
        sites.extend(scan_source(rel, src))
    return sites, parsed


def _key(site: dict) -> tuple[str, str, str]:
    return (site["file"], site["kind"], site["reason"])


def test_scanner_parses_the_real_tree_and_is_not_vacuous():
    sites, parsed = collect()
    assert parsed > 100, f"tests/ 只解析了 {parsed} 个文件，扫描器在空转"
    assert len(sites) > 0, "一处不执行申报都没扫到 = 扫描器失明"


def test_every_non_execution_site_is_declared():
    """申报清单双向相等：新增/改名/改 reason/删掉不执行点，一律判红。"""
    sites, _ = collect()
    found = Counter(_key(s) for s in sites)
    assert found == DECLARED, (
        f"tests/ 的不执行申报面与清单不一致："
        f"未申报={sorted(found - DECLARED)} 已消失={sorted(DECLARED - found)}"
    )
    assert sum(found.values()) == DECLARED_TOTAL, (
        f"总数漂移：{sum(found.values())} != {DECLARED_TOTAL}"
    )


def test_no_abstention_is_conditioned_on_the_behavior_under_test():
    """弃权只许因为环境缺能力，不许因为被测函数没产出（续16 BL-03 的真实掩盖）。

    两条子判据都保留，因为绕过成本不同：
    - 有守卫的弃权：守卫条件必须引用环境能力谓词，否则就是「按被测返回值弃权」；
    - 无守卫的运行时 `pytest.skip()`：必须落在 `UNGUARDED_SKIP_KEYS` 申报集内。
    """
    sites, _ = collect()
    offenders = [
        f"{s['file']}:{s['lineno']} {s['kind']}({s['reason']}) guard={s['guard'][:60]!r}"
        for s in sites
        if s["guard"] and not s["environmental"]
    ]
    assert not offenders, "以下弃权点的守卫不是环境能力，会把红灯读成绿灯：" + " | ".join(offenders)

    unguarded = [
        f"{s['file']}:{s['lineno']} {s['reason']}"
        for s in sites
        if s["kind"] == "pytest.skip" and not s["guard"] and _key(s) not in UNGUARDED_SKIP_KEYS
    ]
    assert not unguarded, "新增未申报的无条件弃权：" + " | ".join(unguarded)


def test_scanner_detects_a_planted_mask_and_a_planted_declaration():
    """扫描器自证有牙：内联样本必须同时抓到装饰器申报与「凭返回值弃权」。"""
    src = (
        "import pytest\n"
        "\n"
        "@pytest.mark.skip(reason='演示用')\n"
        "def test_a():\n"
        "    pass\n"
        "\n"
        "def test_b(report):\n"
        "    if not report.failure_belief_candidates:\n"
        "        pytest.skip('没有产出就跳过')\n"
    )
    sites = scan_source("inline/test_demo.py", src)
    keys = {_key(s) for s in sites}
    assert ("inline/test_demo.py", "pytest.mark.skip", "演示用") in keys, (
        f"装饰器申报没扫到：{keys}"
    )
    assert ("inline/test_demo.py", "pytest.skip", "没有产出就跳过") in keys, (
        f"运行时弃权没扫到：{keys}"
    )
    bad = [s for s in sites if s["guard"] and not s["environmental"]]
    assert [s["reason"] for s in bad] == ["没有产出就跳过"], f"行为弃权未被点名：{bad}"
    decor = [s for s in sites if s["kind"] == "pytest.mark.skip"]
    assert len(decor) == 1 and decor[0]["guard"] == "", f"装饰器申报形态漂移：{decor}"


def test_compliant_environment_guard_is_not_flagged():
    """反向保护：环境能力式 skip 不许被误判成掩盖（防止规则被反向削成摆设）。"""
    src = (
        "import pytest\n"
        "from pathlib import Path\n"
        "\n"
        "def test_a(tmp_path):\n"
        "    if not Path('data/x.json').exists():\n"
        "        pytest.skip('数据集未下载')\n"
    )
    sites = scan_source("inline/test_env.py", src)
    assert len(sites) == 1
    assert sites[0]["environmental"] is True, f"环境式 skip 被误判：{sites}"
    assert [x for x in sites if x["guard"] and not x["environmental"]] == []


def test_unguarded_abstention_must_be_declared():
    """反向对照：能力探测后的无条件 skip 只在申报集内才放行，新增即判红。"""
    src = "import pytest\n\ndef test_a():\n    pytest.skip('本机没有某个可执行文件')\n"
    sites = scan_source("inline/test_unguarded.py", src)
    assert len(sites) == 1 and sites[0]["guard"] == ""
    key = _key(sites[0])
    assert key not in UNGUARDED_SKIP_KEYS, "申报集不该凭空多一条"
    assert key not in set(DECLARED.elements()), "内联样本不该混进真实申报面"
