"""票 32：读侧时间语义漂移——aware 输入裸绑定按墙上时钟当 UTC、日期窗与
每日桶按 UTC 日历日切。

三处病灶（立案基线 `5822840f`）：
1. `chenfeng.search_archived` 把 +08:00 aware `time_from/time_to` 直接绑定
   SQLite——sqlmodel 0.0.42 下 aware 绑定不做偏移换算，+08:00 的墙上时钟
   被当 UTC 文本与库内 UTC 值比较：北京「11-01 全天」语义窗口应为
   [10-31 16:00, 11-01 16:00) UTC，实际比较 [11-01 00:00, 11-02 00:00) UTC
   ——北京凌晨 0-8 点的记忆漏掉、次日凌晨的混入，静默双向漂移 8 小时。
2. `qijuzhu.get_daily_chronicle` 的「今日」= `utcnow().date()`（UTC 日历日）
   ——北京 00:00–08:00 的活动落进昨天的纪事；显式传 date 的窗口也是 UTC 日。
3. `usage.collect_usage` 每日桶 `func.date(created_at)` 配 UTC 日——北京
   00:00–08:00 的写入落前一天桶。

六臂：
- chenfeng 漏掉臂：北京 11-01 04:00（= 10-31 20:00 UTC）的归档记忆查
  北京「11-01 全天」必须命中（现状漂移漏掉）。
- chenfeng 混入臂：北京 11-02 02:00（= 11-01 18:00 UTC）的归档记忆查
  北京「11-01 全天」必须不出现（现状混入）。工单红测字面种子
  「11-01 18:00 UTC 必须命中」与病灶描述的语义矛盾（它属北京 11-02），
  本臂按正确语义断言不出现——禁把病灶编码进期望值。
- chenfeng naive 契约锁（绿）：naive 输入按 UTC 解释（ensure_aware 同
  temporal._ensure_utc 行为），现状即绿，锁住修复不改变 naive 契约。
- qijuzhu 显式日期臂：本地日 00:30 / 23:30 两端种子都必须落入该日期的
  纪事（UTC 日窗下正偏移宿主漏凌晨端、负偏移宿主漏日末端）。
- qijuzhu 默认今日臂：冻结 now 注入缝（`now=` 参数）下「今日」取
  now 的本地日（UTC 20:00 = 北京次日 04:00，date 键须翻转）。
- usage 桶臂：本地日两端种子都落「今天」桶，前一天桶必须为零。

UTC 宿主（offset == 0）上本地日与 UTC 日重合、aware 漂移为零，三处病灶
不可复现（现状即正确）——skip 带由不脏写，强制实跑设
LANTAI_READSIDE_TZ_ARM_FORCE=1。

不 mock 被测链路：真内存 SQLite、真 ChenfengEngine/QijuzhuEngine/
collect_usage，仅隔离 DB（同 tests/test_digest.py 工装）。
"""

import os
from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.cognition.chenfeng import ChenfengEngine
from lantai.cognition.qijuzhu import QijuzhuEngine
from lantai.models.tables import MemoryItem
from lantai.ops.usage import collect_usage

_BEIJING = timezone(timedelta(hours=8))

_utc_offset = datetime.now().astimezone().utcoffset()
_local_tzinfo = datetime.now().astimezone().tzinfo


# ── 工装（同 tests/test_digest.py：内存库 + patch get_session，不 mock 内部逻辑）──


@pytest.fixture()
def readside_env(tmp_path, monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session（三引擎同源）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory


def _seed_memory(session_factory, mem: MemoryItem) -> None:
    with session_factory() as s:
        s.add(mem)
        s.commit()


def _mem(id: str, created_at: datetime, *, status: str = "active") -> MemoryItem:
    return MemoryItem(
        id=f"mem_{id}",
        content=f"票32种子-{id}",
        lane="fact",
        decay_class="episodic",
        importance=0.8,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


# ── chenfeng：aware 时间窗绑定（宿主无关——偏移显式在输入里）──

_BEIJING_DAY = (
    datetime(2026, 11, 1, 0, 0, tzinfo=_BEIJING),  # 北京 11-01 00:00
    datetime(2026, 11, 2, 0, 0, tzinfo=_BEIJING),  # 北京 11-02 00:00
)


def test_chenfeng_time_window_early_morning_not_missed(readside_env):
    """漏掉臂：北京 11-01 04:00（= 10-31 20:00 UTC）的归档记忆，查北京
    「11-01 全天」必须命中。

    现状：+08:00 墙上时钟被当 UTC 文本绑定，实际比较窗口
    [11-01 00:00, 11-02 00:00) UTC——种子在其外，静默漏掉（红）。
    """
    session_factory = readside_env
    _seed_memory(
        session_factory,
        _mem("凌晨四点", datetime(2026, 10, 31, 20, 0, tzinfo=UTC), status="archived"),
    )

    result = ChenfengEngine().search_archived(time_from=_BEIJING_DAY[0], time_to=_BEIJING_DAY[1])
    ids = [item["id"] for item in result["items"]]
    assert "mem_凌晨四点" in ids, (
        f"北京 11-01 04:00 的归档记忆未命中北京「11-01 全天」查询——"
        f"aware 时间绑定按墙上时钟当 UTC，窗口静默漂移 +8h（票 32 病灶 1）；ids={ids}"
    )


def test_chenfeng_time_window_next_day_not_mixed_in(readside_env):
    """混入臂：北京 11-02 02:00（= 11-01 18:00 UTC）的归档记忆，查北京
    「11-01 全天」必须不出现。

    现状：漂移窗口 [11-01 00:00, 11-02 00:00) UTC 把它当 11-01 的混进来
    （红）。工单红测字面种子「11-01 18:00 UTC 必须命中」与病灶描述矛盾
    （该时刻属北京 11-02）——按正确语义断言不出现，禁把病灶编码进期望值。
    """
    session_factory = readside_env
    _seed_memory(
        session_factory,
        _mem("次日凌晨", datetime(2026, 11, 1, 18, 0, tzinfo=UTC), status="archived"),
    )

    result = ChenfengEngine().search_archived(time_from=_BEIJING_DAY[0], time_to=_BEIJING_DAY[1])
    ids = [item["id"] for item in result["items"]]
    assert "mem_次日凌晨" not in ids, (
        f"北京 11-02 02:00 的归档记忆混入北京「11-01 全天」查询——"
        f"aware 时间绑定漂移的混入半边（票 32 病灶 1）；ids={ids}"
    )
    assert result["total"] == 0, "北京 11-01 全天窗口内无种子，total 应为 0"


def test_chenfeng_naive_input_treated_as_utc(readside_env):
    """naive 契约锁（绿）：naive time_from/time_to 按 UTC 解释（与
    temporal._ensure_utc / core.time.ensure_aware 行为一致）。现状即绿——
    锁住修复不改变 naive 契约。"""
    session_factory = readside_env
    _seed_memory(
        session_factory,
        _mem("utc十点", datetime(2026, 11, 1, 10, 0, tzinfo=UTC), status="archived"),
    )

    result = ChenfengEngine().search_archived(
        time_from=datetime(2026, 11, 1, 0, 0),  # naive：按 UTC 解释
        time_to=datetime(2026, 11, 2, 0, 0),
    )
    ids = [item["id"] for item in result["items"]]
    assert "mem_utc十点" in ids, f"naive 输入按 UTC 解释的契约被破坏；ids={ids}"


def test_chenfeng_offset_and_utc_representations_equivalent(readside_env):
    """对照臂（等价锁，票 32 TDD 要求 2）：同一窗口的 +08:00 表示与 UTC
    表示必须给出同一结果——归一后两表示等价。

    修复前该等价性被病灶破坏（+08:00 形态漂移，漏掉/混入两臂已分别实证
    红）；本锁钉死修复后两种表示永不分叉。"""
    session_factory = readside_env
    _seed_memory(
        session_factory,
        _mem("窗内", datetime(2026, 10, 31, 20, 0, tzinfo=UTC), status="archived"),
    )
    _seed_memory(
        session_factory,
        _mem("窗外", datetime(2026, 11, 1, 18, 0, tzinfo=UTC), status="archived"),
    )

    offset_form = ChenfengEngine().search_archived(
        time_from=datetime(2026, 11, 1, 0, 0, tzinfo=_BEIJING),
        time_to=datetime(2026, 11, 2, 0, 0, tzinfo=_BEIJING),
    )
    utc_form = ChenfengEngine().search_archived(
        time_from=datetime(2026, 10, 31, 16, 0, tzinfo=UTC),
        time_to=datetime(2026, 11, 1, 16, 0, tzinfo=UTC),
    )
    ids_offset = [item["id"] for item in offset_form["items"]]
    ids_utc = [item["id"] for item in utc_form["items"]]
    assert ids_offset == ids_utc == ["mem_窗内"], (
        f"同一窗口的 +08:00 表示与 UTC 表示结果分叉——归一失败回退："
        f"offset={ids_offset} utc={ids_utc}"
    )


# ── qijuzhu：本地日窗口（UTC 宿主语义重合，skip 带由）──


@pytest.mark.skipif(
    _utc_offset == timedelta(0) and os.environ.get("LANTAI_READSIDE_TZ_ARM_FORCE") != "1",
    reason=(
        "readside 本地日语义红测需宿主时区 ≠ UTC 才能复现 UTC 日错位：UTC 宿主上"
        "本地日与 UTC 日重合、aware 偏移为零，现状即正确（语义重合非假绿）——"
        "宁 skip 带由不脏写；强制实跑设 LANTAI_READSIDE_TZ_ARM_FORCE=1"
    ),
)
def test_qijuzhu_explicit_date_uses_local_day(readside_env):
    """显式日期臂：本地日 00:30 / 23:30 两端种子都必须落入该日期的纪事，
    本地日界外一秒不得混入。

    现状（UTC 日窗 [D 00:00, D+1 00:00) UTC）：+8 宿主漏本地凌晨端
    （00:30 北京 = 前一日 16:30 UTC）、-偏移宿主漏日末端——两端 AND
    断言现状必红。"""
    session_factory = readside_env
    target = datetime(2026, 11, 1).date()
    day_start = datetime.combine(target, time.min, tzinfo=_local_tzinfo)
    day_end = day_start + timedelta(days=1)
    _seed_memory(
        session_factory, _mem("凌晨端", (day_start + timedelta(minutes=30)).astimezone(UTC))
    )
    _seed_memory(session_factory, _mem("日末端", (day_end - timedelta(minutes=30)).astimezone(UTC)))
    _seed_memory(session_factory, _mem("前夜", (day_start - timedelta(seconds=1)).astimezone(UTC)))
    _seed_memory(session_factory, _mem("翌晨", (day_end + timedelta(seconds=1)).astimezone(UTC)))

    result = QijuzhuEngine().get_daily_chronicle(date=target.isoformat())
    ref_ids = {e.get("ref_id") for e in result["chronicle_events"]}
    assert {"mem_凌晨端", "mem_日末端"} <= ref_ids, (
        f"本地日两端的记忆未全部落入该日期纪事——窗口按 UTC 日历日切，"
        f"北京凌晨/日末活动落错日（票 32 病灶 2）；ref_ids={ref_ids}"
    )
    assert "mem_前夜" not in ref_ids and "mem_翌晨" not in ref_ids, (
        f"本地日界外一秒混入纪事——窗口越界；ref_ids={ref_ids}"
    )


@pytest.mark.skipif(
    _utc_offset == timedelta(0) and os.environ.get("LANTAI_READSIDE_TZ_ARM_FORCE") != "1",
    reason=(
        "readside 本地日语义红测需宿主时区 ≠ UTC 才能复现 UTC 日错位：UTC 宿主上"
        "本地日与 UTC 日重合、aware 偏移为零，现状即正确（语义重合非假绿）——"
        "宁 skip 带由不脏写；强制实跑设 LANTAI_READSIDE_TZ_ARM_FORCE=1"
    ),
)
def test_qijuzhu_default_today_uses_local_day(readside_env):
    """默认今日臂：冻结 now 注入缝（`now=` 参数）下「今日」取 now 的
    本地日——UTC 2026-11-01 20:00 = 北京 11-02 04:00，date 键须翻到
    11-02（UTC 日键 11-01 是病灶形态）。

    现状 get_daily_chronicle 无 now 缝（TypeError 确定性红）；修复后
    行为断言接管。"""
    session_factory = readside_env
    frozen_now = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)  # 北京 11-02 04:00
    local_date = frozen_now.astimezone(_local_tzinfo).date()
    day_start = datetime.combine(local_date, time.min, tzinfo=_local_tzinfo)
    day_end = day_start + timedelta(days=1)
    _seed_memory(session_factory, _mem("今晨", (day_start + timedelta(minutes=30)).astimezone(UTC)))
    _seed_memory(session_factory, _mem("今晚", (day_end - timedelta(minutes=30)).astimezone(UTC)))

    result = QijuzhuEngine().get_daily_chronicle(now=frozen_now)
    assert result["date"] == local_date.isoformat(), (
        f"「今日」应为 now 的本地日 {local_date.isoformat()}，实得 "
        f"{result['date']}——UTC 日历日键（票 32 病灶 2 的日期翻转面）"
    )
    ref_ids = {e.get("ref_id") for e in result["chronicle_events"]}
    assert {"mem_今晨", "mem_今晚"} <= ref_ids, (
        f"本地日两端记忆未全部落入默认今日纪事；ref_ids={ref_ids}"
    )


# ── usage：每日桶本地日（UTC 宿主语义重合，skip 带由）──


@pytest.mark.skipif(
    _utc_offset == timedelta(0) and os.environ.get("LANTAI_READSIDE_TZ_ARM_FORCE") != "1",
    reason=(
        "readside 本地日语义红测需宿主时区 ≠ UTC 才能复现 UTC 日错位：UTC 宿主上"
        "本地日与 UTC 日重合、aware 偏移为零，现状即正确（语义重合非假绿）——"
        "宁 skip 带由不脏写；强制实跑设 LANTAI_READSIDE_TZ_ARM_FORCE=1"
    ),
)
def test_usage_daily_bucket_uses_local_day(readside_env):
    """桶臂：本地日 00:30 / 23:30 两端的写入都必须落「今天」桶，前一天桶
    必须为零。

    现状（UTC 日桶）：+8 宿主凌晨端写入落前一天桶（func.date 按 UTC 日
    切）——daily[today] 少计、daily[yesterday] 多计（红）。"""
    session_factory = readside_env
    local_today = datetime.now().astimezone().date()
    day_start = datetime.combine(local_today, time.min, tzinfo=_local_tzinfo)
    day_end = day_start + timedelta(days=1)
    _seed_memory(
        session_factory, _mem("今晨写入", (day_start + timedelta(minutes=30)).astimezone(UTC))
    )
    _seed_memory(
        session_factory, _mem("今晚写入", (day_end - timedelta(minutes=30)).astimezone(UTC))
    )

    result = collect_usage(days=9)
    daily = result["daily_new"]
    assert daily[str(local_today)] == 2, (
        f"本地日两端的写入应全落「今天」桶（2 条），实得 {daily[str(local_today)]}"
        f"——每日桶按 UTC 日切，北京凌晨写入落前一天（票 32 病灶 3）"
    )
    assert daily[str(local_today - timedelta(days=1))] == 0, (
        f"前一天桶应为零，实得 {daily[str(local_today - timedelta(days=1))]}——"
        "凌晨写入被错记到前一天桶"
    )
