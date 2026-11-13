r"""票 53：datetime 序列化出口双形态收敛（iso_utc 单一真源）。

同一概念字段（created_at/updated_at/generated_at 等）在不同出口序列化
形态不同：形态 A（utcnow() aware → `+00:00` 后缀）、形态 B（DB 列读回
naive → 无后缀），部分站点还叠 `timespec="seconds"` 截掉微秒——第三形态。
JS 消费端 `new Date("…")` 无后缀按本地时区解释、带 `+00:00` 按 UTC 解释，
悬镜面板两类字段同屏差 8 小时（Asia/Shanghai）。

修复口径（督导拍板）：`core/time.py::iso_utc(dt)` 与 `parse_iso_utc` 对称
成单一真源——naive 按「naive=UTC」约定 `replace(tzinfo=UTC)` 补后缀
（**绝不可走 astimezone**：naive 调 astimezone 会按本地时区 Asia/Shanghai
解释平移 +8h，墙钟数字全变）；aware 一律 `astimezone(UTC)` 折算；
`timespec="microseconds"` 全精度（Python isoformat 在 microsecond==0 时
省略小数段，不显式给 timespec 会分裂出第三形态）。

目标形态正则：`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$`。

四臂：a) 哨兵 AST 扫全 lantai 裸 `.isoformat(` 未走 iso_utc 即红；b) 行为锁
——candidate_service（形态A）与 memory_service（形态B）出口同形态同精度；
c) naive 不漂移臂；d) 零微秒臂。禁 mock 被测链路。
"""

import ast
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from lantai.models.tables import MemoryItem

# 目标形态：全精度微秒 + UTC 后缀
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")

# ── 臂 c/d：iso_utc 本体三陷阱（naive 不漂移 / aware 折算 / 零微秒）────────


def test_iso_utc_naive_wall_clock_unchanged():
    """红（臂 c）：naive 绝不可走 astimezone——本地时区 Asia/Shanghai 解释
    会平移 +8h。修复后墙钟数字不变、仅补 +00:00。"""
    from lantai.core.time import iso_utc

    naive = datetime(2026, 11, 7, 12, 30, 45, 123456)  # DB 读回形态
    out = iso_utc(naive)
    assert out == "2026-11-07T12:30:45.123456+00:00", (
        f"naive 必须按 naive=UTC 补后缀、墙钟数字不变（astimezone 会 +8h）：{out!r}"
    )


def test_iso_utc_aware_converts_to_utc():
    """臂 c 对照：aware (+08:00) 正确折算 UTC（时刻不变、墙钟变）。"""
    from lantai.core.time import iso_utc

    aware = datetime(2026, 11, 7, 12, 30, 45, 123456, tzinfo=timezone(timedelta(hours=8)))
    out = iso_utc(aware)
    assert out == "2026-11-07T04:30:45.123456+00:00", f"aware 应折算 UTC：{out!r}"


def test_iso_utc_zero_microsecond_keeps_fraction():
    """红（臂 d）：Python isoformat 在 microsecond==0 时省略小数段——不显式
    timespec 会分裂出「有时无分数」的第三形态。修复后恒输出 .000000。"""
    from lantai.core.time import iso_utc

    out = iso_utc(datetime(2026, 11, 7, 12, 30, 45, 0, tzinfo=UTC))
    assert _ISO_UTC_RE.match(out), f"零微秒仍须输出 .000000 全精度形态：{out!r}"


def test_iso_utc_none_passthrough():
    """None → ""（nullable 列出口惯例，与既有站点三元形态一致）。"""
    from lantai.core.time import iso_utc

    assert iso_utc(None) == ""


# ── 臂 b：行为锁——形态 A/B 出口同形态同精度 ─────────────────────────────


def test_candidate_and_memory_exits_same_form(param_env):
    """红（臂 b）：candidate_service（形态 A aware → +00:00）与
    memory_service（形态 B naive → 无后缀 + 截秒）两出口同调同形态。
    修复后双双命中目标正则。"""
    from lantai.services.candidate_service import defer_candidate
    from lantai.services.memory_service import build_memories_page

    sf, _engine = param_env
    with sf() as s:
        m = MemoryItem(
            id="m53",
            content="票53行为锁记忆内容",
            status="active",
            # principal=None 时 build_memories_page 收敛 viewer="default"——
            # 行属主须为 default 才可见（票 .scratch/mcp-identity-gaps/02 续口径）
            user_id="default",
            lane="general",
            domain="user",
            decay_score=1.0,
            # 写入面恒 aware（ensure_aware 纪律）；SQLite DateTime 列读回即
            # naive——这正是形态 B 病灶的真实机理（DB 列读回 → 无后缀）
            created_at=datetime(2026, 11, 7, 12, 30, 45, 123456, tzinfo=UTC),
            updated_at=datetime(2026, 11, 7, 12, 30, 45, 123456, tzinfo=UTC),
        )
        s.add(m)
        s.commit()

        page = build_memories_page(session=s, limit=10)
        rows = page.get("memories") or page.get("rows") or page.get("items") or []
        assert rows, "build_memories_page 应返回行（真链路直调）"
        row = rows[0]
        created = row["created_at"]
        assert isinstance(created, str), f"created_at 出口应为 str：{created!r}"
        assert _ISO_UTC_RE.match(created), (
            f"形态 B（memory_service）未收敛：{created!r}（现状 naive 无后缀/截秒）"
        )

    # 形态 A：defer_candidate 返回 review_due_at（UTC 现值 aware → +00:00）
    with sf() as s:
        from lantai.models.tables import MemoryCandidate

        cand = MemoryCandidate(
            id="c53",
            document_id="doc53",
            summary="票53候选",
            status="pending_review",
            user_id="u53",
            created_at=datetime(2026, 11, 7, 12, 30, 45, tzinfo=UTC),
        )
        s.add(cand)
        s.commit()
    res = defer_candidate("c53", days=7, principal=None)
    due = res["review_due_at"]
    assert _ISO_UTC_RE.match(due), f"形态 A（candidate_service）未收敛：{due!r}"


# ── 臂 a：哨兵——全仓裸 .isoformat( 未走 iso_utc 即红 ──────────────────────

# 白名单（file, 行号）：非 datetime 的 isoformat（date 对象/内部用途）逐条登记。
# datetime 出口一律 iso_utc；date 对象无时区概念、isoformat 天然无后缀，不在本票。
_WHITELIST: set[tuple[str, int]] = {
    # qijuzhu target_date 是 date 对象（get_chronicle 入参 date）：日历日无时区
    # （票 67：双轨拆分 _get_daily_chronicle 后行号 253/264 → 266/277；
    #  自审删除零调用方 _get_session 死壳后再漂 5 行 → 261/272）
    ("cognition/qijuzhu.py", 261),
    ("cognition/qijuzhu.py", 272),
    # eval 基准集日期解析（naive strptime 补 +00:00，非产品 datetime 出口）：
    # parse_lme_date 产物进 import_lines created_at，eval 语料定置 hash 要求
    # 串形稳定（"2023-05-20T02:21:00" 无小数段），改 iso_utc 全精度会漂移
    # 基线比对——eval 语料面豁免，产品面仍一律 iso_utc
    ("eval/longmemeval.py", 126),
    # utcnow().date() —— 标题字符串里的日历日，非 datetime 出口
    ("services/distill_service.py", 188),
    # digest_worker day 是 date 对象（MCP date 序列化，票 52 已收口）；
    # 票 84：移除日窗复制实现后，三个 date 序列化点随行平移；
    # 白名单仍精确到 (file, line)，不扩大豁免范围。
    ("workers/digest_worker.py", 322),
    ("workers/digest_worker.py", 600),
    ("workers/digest_worker.py", 606),
    # 票 53 审查修正：SQL cutoff 比较操作数（operation_logs.created_at 落盘为
    # 空格分隔形态，isoformat(sep=" ") 与之同形；走 iso_utc 的 T 分隔会让
    # 同日历日行提前 ~23h 被剪）。SQL 操作数非 datetime JSON 出口。
    ("observability/telemetry.py", 136),
}

# 白名单按 (file, 行号) 精确登记；AST 扫描取 Call.func.value 为 Attribute
# 且 attr == "isoformat" 的调用点。


def _isoformat_violations():
    import lantai

    pkg_root = Path(lantai.__file__).parent
    violations = []
    for py in sorted(pkg_root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(pkg_root).as_posix()
        if rel == "core/time.py":
            continue  # 本体豁免
        src = py.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "isoformat"
            ):
                if (rel, node.lineno) in _WHITELIST:
                    continue
                violations.append(f"{rel}:{node.lineno}")
    return violations


def test_isoformat_sentinel():
    """红（臂 a）：全仓 .isoformat( 调用点必须收敛到 iso_utc（或白名单登记）。

    白名单登记规则：仅 date 对象的 isoformat（date 无时区概念）与纯内部
    非出口用途逐条登记；datetime 出口一律 iso_utc。"""
    violations = _isoformat_violations()
    assert not violations, (
        "裸 .isoformat( 调用点（datetime 出口须走 core.time.iso_utc，date/内部"
        "用途白名单登记）：\n" + "\n".join(violations)
    )
