"""票 31：每日盘点时间窗差一天——触发点远离本地日界，窗口与触发错位。

病灶（立案基线 d2d784fe）：DIGEST_CRON_HOUR=22（UTC）= 北京 06:00 触发，
而报告窗口是触发当日本地日历日 [D 00:00, D+1 00:00)——北京用户早晨 6 点
拿到的"日报"只覆盖当天 0 点到 6 点的 6 小时，前一天的 18 小时数据永远
落进下一份报告再被下一份覆盖，永远不出现在任何一份"当日"报告里。

七臂：
- 配置契约臂：触发点必须贴近它所报告的那个本地日的日界（上海语义）。
- 注册哨兵臂（AST）：digest cron 注册必须显式传 minute=——漏了则
  _DIGEST_CRON_MINUTE 单一真源对 cron 注册面失效（minute 默认 0）。
- 两段到达臂：触发前落库的数据进当日报告，触发后落库的数据必须出现在
  某份报告里（现状：永远丢失）。需宿主时区 UTC+8 才忠实复现部署语义，
  非 +8 宿主 skip 带由（宁 skip 不脏写）。
- 补跑锚定臂：错过调度点后的启动补跑必须补"错过的那个触发点所报告的
  本地日"，不锚启动时刻的当天。
- 全天覆盖锁（绿）：显式 day 下，本地日 0:30 / 12:00 / 23:30 全部计入。
- 消费端现状锁（绿）：裸触发写出的文件 = `load_today_digest`"今日"指向的
  同一份——案 A 下触发日末写当日，消费端语义不打架（案 B 才需改此断言）。
- 边界跨越锁（绿）：日界两侧各一条，各归各的报告——窗口数学的回归锁。

不 mock 被测链路：真内存 SQLite、真 run_digest_once / _catch_up_daily_jobs，
仅隔离 DB 与报告输出目录（同 tests/test_digest.py 工装）。
"""

import ast
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core import scheduler as sched_mod
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem

_SHANGHAI = ZoneInfo("Asia/Shanghai")


# ── 工装（同 tests/test_digest.py：内存库 + 输出目录隔离，不 mock 内部逻辑）──


@pytest.fixture()
def digest_env(tmp_path, monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session + 报告输出目录隔离。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", str(tmp_path / "digest"))
    return session_factory


def _seed(session_factory, rows):
    with session_factory() as s:
        for row in rows:
            s.add(row)
        s.commit()


def _mem(key: str, created_at: datetime) -> MemoryItem:
    return MemoryItem(
        id=f"mem_{key}",
        memory_type="semantic",
        key=key,
        content=f"票31种子-{key}",
        created_at=created_at,
        updated_at=created_at,
    )


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def _added_count(content: str) -> int:
    line = next(l for l in content.splitlines() if l.startswith("| 新增记忆 |"))
    return int(line.split("|")[2].strip())


def _report(day_iso: str) -> Path:
    return Path(settings.DIGEST_OUTPUT_DIR) / f"{day_iso}.md"


def _cron_minute() -> int:
    """cron 分钟位单一真源（scheduler 模块属性直读，改常量测试自动跟随）。"""
    return sched_mod._DIGEST_CRON_MINUTE


def _most_recent_fire_utc(now_utc: datetime, hour: int, minute: int) -> datetime:
    """最近一次已到点的每日调度时刻（UTC）——票面口径的独立复现，
    与生产 should_catch_up 同语义：now 未到今日触发点则取昨日。"""
    today_fire = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today_fire if now_utc >= today_fire else today_fire - timedelta(days=1)


def _fire_local_for(day):
    """报告 day 那份日报的触发时刻（系统本地时区）：UTC cron 点落到系统
    本地后若已跨到 day+1，则回退一天（现状 UTC 22:00 = 北京次日 06:00）。"""
    candidate_utc = datetime.combine(
        day, time(settings.DIGEST_CRON_HOUR, _cron_minute()), tzinfo=UTC
    )
    local = candidate_utc.astimezone()
    if local.date() == day:
        return local
    return (candidate_utc - timedelta(days=1)).astimezone()


# ── 配置契约臂（红）：触发点必须贴近它所报告的本地日的日界 ──


def test_digest_cron_fires_near_shanghai_day_end():
    """案 A 拍板：UTC 15:55 = 上海本地 23:55，距日界 5 分钟。

    现状 UTC 22:00 = 上海 06:00——触发点距它所报告日子的日界还有 18 小时，
    当天 06:00 之后落库的数据注定缺席"当日"报告。宿主无关：纯 ZoneInfo
    时区数学，锁定的是产品决策（北京早晨用户看到的是完整的前一日盘点）。
    """
    fire_utc = datetime.combine(
        datetime.now(UTC).date(),
        time(settings.DIGEST_CRON_HOUR, _cron_minute()),
        tzinfo=UTC,
    )
    fire_sh = fire_utc.astimezone(_SHANGHAI)
    day_end = datetime.combine(fire_sh.date() + timedelta(days=1), time.min, tzinfo=_SHANGHAI)
    tail = day_end - fire_sh
    assert fire_sh.hour == 23, (
        f"触发点上海本地时刻 {fire_sh:%H:%M} 不在日界前的 23 时段——报告窗口与触发错位（票 31 病灶）"
    )
    assert tail <= timedelta(minutes=10), (
        f"触发点距本地日界还有 {tail}——当日 {fire_sh:%H:%M} 之后落库的数据将缺席当日报告"
    )


# ── 注册哨兵臂（红）：cron 注册必须显式传 minute= ──


def test_digest_cron_registration_wires_minute():
    """scheduler.py 的 digest cron 注册必须带 minute= 关键字。

    现状只传 hour=——APScheduler minute 默认 0，_DIGEST_CRON_MINUTE 对
    cron 注册面是死配置；改常量不动注册面则修复无效（哑弹锁）。
    """
    source = Path(sched_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    digest_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_job"):
            continue
        ids = [
            kw.value.value
            for kw in node.keywords
            if kw.arg == "id" and isinstance(kw.value, ast.Constant)
        ]
        if "digest" in ids:
            digest_calls.append(node)
    assert len(digest_calls) == 1, f"digest add_job 调用应恰一处，实得 {len(digest_calls)}"
    call = digest_calls[0]
    minute_kws = [kw for kw in call.keywords if kw.arg == "minute"]
    assert minute_kws, (
        "digest cron 注册缺 minute= 关键字——_DIGEST_CRON_MINUTE 对注册面失效，"
        "APScheduler 按 minute=0 触发（票 31 哑弹锁）"
    )


# ── 两段到达臂（红）：触发后落库的数据必须出现在某份报告里 ──


@pytest.mark.skipif(
    datetime.now().astimezone().utcoffset() != timedelta(hours=8)
    and os.environ.get("LANTAI_DIGEST_TZ_ARM_FORCE") != "1",
    reason=(
        "案 A 的触发-窗口组合行为需宿主时区 UTC+8（Asia/Shanghai 部署语义）才能"
        "忠实复现：非 +8 宿主上窗口锚定系统时区、语义不同——宁 skip 带由不脏写；"
        "配置契约与补跑锚定两臂宿主无关已覆盖其余面；非 +8 宿主如需强制实跑本臂"
        "（如 CI 验证 skip 逻辑），设 LANTAI_DIGEST_TZ_ARM_FORCE=1"
    ),
)
def test_two_phase_arrival_all_captured(digest_env):
    """真实 cron 形态（裸 run_digest_once，day=None 锚触发时刻本地日）下的
    数据到达模型：触发前落库 → 触发生成当日报告 → 触发后落库 → 下次触发
    生成次日报告。断言：所有种子都出现在某份报告里，无一粒凭空丢失。

    现状（触发上海 06:00）：触发后到达的 12:00/20:00 两粒永远丢失——
    当日报告已写定、次日报告窗口不含它们（红测实证 captured 1 != 3）。
    修复后（触发上海 23:55）：种子全在触发前到达（post 为空即是修复生效
    的形态），单段全收；若未来有人把触发点改回日间（tail 复活），post
    非空、两段丢失检查重新咬合。
    """
    session_factory = digest_env
    from lantai.workers.digest_worker import run_digest_once

    day = datetime.now().astimezone().date()
    day_end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_local_tzinfo())
    seeds = {
        "s0030": datetime.combine(day, time(0, 30), tzinfo=_local_tzinfo()),
        "s1200": datetime.combine(day, time(12, 0), tzinfo=_local_tzinfo()),
        "s2000": datetime.combine(day, time(20, 0), tzinfo=_local_tzinfo()),
    }
    assert day_end_local - seeds["s2000"] > timedelta(0), "种子时刻须落在 day 日界内"

    fire1 = _fire_local_for(day)
    assert fire1.date() == day, (
        "触发点的本地日必须就是它所报告的日子——裸调用 day=None 锚的是触发"
        "时刻的本地日，锚错日期则全日数据进错报告（票 31 病灶的日期翻转面）"
    )
    pre = [k for k, t in seeds.items() if t < fire1]
    post = [k for k, t in seeds.items() if t >= fire1]
    assert pre, f"触发前至少一粒种子（pre={pre}, post={post}）"

    # 第一段：触发前到达的数据落库，触发点跑一次（裸调用，同生产 cron）
    _seed(session_factory, [_mem(k, seeds[k].astimezone(UTC)) for k in pre])
    result1 = run_digest_once()
    assert result1["day"] == day.isoformat(), (
        f"裸调用的报告日 {result1['day']} 应为触发点本地日 {day.isoformat()}"
    )

    # 第二段：仅当触发点后仍有当日窗口（日间触发的回归形态）才成立——
    # 触发后到达的数据落库，下次触发跑一次（锚下次触发的本地日）
    if post:
        _seed(session_factory, [_mem(k, seeds[k].astimezone(UTC)) for k in post])
        fire2 = _fire_local_for(day + timedelta(days=1))
        run_digest_once(day=fire2.date())

    reports = [
        p
        for p in (
            _report(day.isoformat()),
            _report((day + timedelta(days=1)).isoformat()),
        )
        if p.exists()
    ]
    assert reports, "至少一份报告落盘"
    captured = sum(_added_count(p.read_text(encoding="utf-8")) for p in reports)
    assert captured == len(seeds), (
        f"三粒种子仅 {captured} 粒出现在任何报告里——触发点 {fire1:%H:%M} 之后"
        f"落库的数据（{post}）永远缺席'当日'报告（票 31 病灶）"
    )


# ── 补跑锚定臂（红）：补跑补"错过的触发点所报告的本地日" ──


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, fn, trigger, *, run_date, id, replace_existing=False):
        self.jobs[id] = fn


def test_catchup_reports_missed_fire_day(digest_env, monkeypatch):
    """昨天 23:55（案 A 触发点）错过、今晨 08:00 启动——补跑必须补昨天那份
    日报，而不是把启动时刻的半天数据写成"今天"的报告。

    now 显式注入（上海 2026-11-09 08:00），宿主无关。现状
    _catch_up_daily_jobs 不接受 now 且注册裸 run_digest_once（锚启动日）。
    """
    session_factory = digest_env
    monkeypatch.setattr(settings, "DIGEST_ENABLED", True)
    monkeypatch.setattr(sched_mod, "_scheduler", _FakeScheduler())
    missed_day = datetime(2026, 11, 8, 23, 55, tzinfo=_SHANGHAI).date()
    now_local = datetime(2026, 11, 9, 8, 0, tzinfo=_SHANGHAI)
    now_utc = now_local.astimezone(UTC)
    # 上次运行停在两天前（错过昨天的触发点）
    monkeypatch.setattr(
        sched_mod,
        "WORKER_LAST_RUN",
        {"digest": (now_utc - timedelta(days=2)).isoformat()},
    )
    # 错过日的正午落一条种子（宿主本地 12:00，保证落在该日宿主本地窗口内）
    seed_local = datetime.combine(missed_day, time(12, 0), tzinfo=_local_tzinfo())
    _seed(session_factory, [_mem("missed", seed_local.astimezone(UTC))])

    sched_mod._catch_up_daily_jobs(now=now_utc)

    jobs = sched_mod._scheduler.jobs
    assert "digest_catchup" in jobs, f"错过触发点应登记补跑任务，实得 {list(jobs)}"
    jobs["digest_catchup"]()  # 真跑补跑链（DB/输出目录已隔离）

    report = _report(missed_day.isoformat())
    assert report.exists(), (
        f"补跑应锚定错过触发点的本地日 {missed_day}——现状锚启动时刻当天，"
        "错过的那日数据被写成错误日期的残报（票 31 病灶）"
    )
    assert _added_count(report.read_text(encoding="utf-8")) == 1, "补跑报告应含错过日种子"


# ── 全天覆盖锁（绿）：显式 day 下全天种子全部计入 ──


def test_full_day_coverage_explicit_day(digest_env):
    """窗口数学的正面回归锁：显式 day 时本地日 0:30 / 12:00 / 23:30 三粒
    全部计入当日报告。此臂现状即绿——锁住修复不破坏窗口本身。"""
    session_factory = digest_env
    from lantai.workers.digest_worker import run_digest_once

    day = datetime(2026, 11, 1).date()
    for name, hour in (("早", 0), ("午", 12), ("晚", 23)):
        local = datetime.combine(day, time(hour, 30), tzinfo=_local_tzinfo())
        _seed(session_factory, [_mem(name, local.astimezone(UTC))])

    result = run_digest_once(day=day)
    assert result["ok"] is True
    assert result["day"] == day.isoformat()
    assert _report(day.isoformat()).exists()
    content = _report(day.isoformat()).read_text(encoding="utf-8")
    assert _added_count(content) == 3, f"全天三粒种子应全部计入，实得 {content[:200]}"


# ── 消费端现状锁（绿）：触发产物与消费端"今日"语义一致 ──


def test_consumer_today_matches_fire_day_key(digest_env):
    """票 31 TDD 臂 3：`load_today_digest` 语义与文件 day 键一致。

    案 A（触发本地日末 23:55）下触发写出的就是"当日"文件——消费端
    REST/MCP"今日盘点"指向同一份，不需要案 B 的 day 键连带改。两分支
    都锁：缺文件分支按需生成（day=本地今日）、已有文件分支读同一份
    （不重复生成）。"""
    from lantai.workers.digest_worker import load_today_digest, run_digest_once

    today = datetime.now().astimezone().date().isoformat()

    # 缺文件分支：按需生成，day 键 = 本地今日
    consumer = load_today_digest()
    assert consumer["ok"] is True
    assert consumer["day"] == today, f"消费端今日 {consumer['day']} 应为本地今日 {today}"
    assert consumer["path"] == f"{today}.md"

    # 已有文件分支：裸触发（同生产 cron）写出的文件就是消费端读的那份
    fired = run_digest_once()
    consumer2 = load_today_digest()
    assert consumer2["day"] == fired["day"], "触发产物与消费端今日必须同日"
    assert consumer2["path"] == fired["path"] == f"{today}.md"
    assert consumer2["content"] == fired["content"], "消费端应读触发写出的同一份文件"


# ── 边界跨越锁（绿）：日界两侧各归各的报告 ──


def test_window_boundary_straddle(digest_env):
    """日界两侧各一粒：23:59:59 归当日、次日 00:00:01 归次日——
    窗口 [D 00:00, D+1 00:00) 左闭右开的回归锁。另验种子真实落库
    （假绿防线：统计数字不许来自空库巧合）。"""
    session_factory = digest_env
    from sqlmodel import select

    from lantai.workers.digest_worker import run_digest_once

    day = datetime(2026, 11, 1).date()
    inside_local = datetime.combine(day, time(23, 59, 59), tzinfo=_local_tzinfo())
    outside_local = datetime.combine(day + timedelta(days=1), time(0, 0, 1), tzinfo=_local_tzinfo())
    _seed(
        session_factory,
        [
            _mem("界内", inside_local.astimezone(UTC)),
            _mem("界外", outside_local.astimezone(UTC)),
        ],
    )

    with session_factory() as s:
        persisted = len(list(s.exec(select(MemoryItem)).all()))
    assert persisted == 2, f"种子须真实落库（假绿防线），实得 {persisted} 条"

    run_digest_once(day=day)
    run_digest_once(day=day + timedelta(days=1))
    assert _added_count(_report(day.isoformat()).read_text(encoding="utf-8")) == 1, (
        "23:59:59 应归当日"
    )
    assert (
        _added_count(_report((day + timedelta(days=1)).isoformat()).read_text(encoding="utf-8"))
        == 1
    ), "次日 00:00:01 应归次日报告"
