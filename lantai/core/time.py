import logging
import re
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime | None) -> datetime | None:
    """补时区：naive 按 UTC 解释，aware 归一为 UTC；None 原样返回。

    SQLModel 0.0.45 起 datetime 默认使用 UTCDateTime：拒绝 naive 写入及
    查询参数，带偏移输入换算 UTC，读回 aware UTC（包括 SQLite）。本函数
    保留项目导入约定：明确表示 UTC 的旧 naive 值先补时区再进入数据库；
    字符串/provenance 则经 parse_iso_utc。历史 SQLite 已存 naive UTC，
    升级不需要转换数据。此处只归一时刻，不修改字段的业务语义：naive 补
    UTC 时墙钟不变，aware 换算 UTC 时墙钟随偏移变化。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_iso_utc(value: str) -> datetime:
    """解析 ISO-8601 字符串 → aware UTC datetime（失败抛 ValueError）。

    与 `ensure_aware` 同语义：带偏移的输入换算到 UTC，无偏移的串按 UTC 解释
    （兰台约定：全部时间戳归一到 UTC）。用于 provenance/请求体里的时间字符串
    要写回 datetime 列的场景——先恢复 aware UTC，再交给 UTCDateTime 绑定；
    旧 JSON 快照的无偏移 UTC 字符串也沿用此约定（见 `ensure_aware`）。
    """
    text = value.strip()
    if not text:
        raise ValueError("empty ISO timestamp")
    return ensure_aware(datetime.fromisoformat(text.replace("Z", "+00:00")))


def iso_utc(dt: datetime | None) -> str:
    """datetime → ISO-8601 UTC 串（票 53，出口单一真源，与 parse_iso_utc 对称）。

    出口统一形态：``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``（全精度微秒 + UTC
    后缀）。此前同概念字段在不同出口序列化形态不同（aware 带 `+00:00`、
    naive 无后缀、部分叠 timespec 截秒），JS `new Date()` 对无后缀串按本地
    时区解释——悬镜面板两类字段同屏差 8 小时。

    - naive（旧快照/原始 SQL）：按「naive=UTC」约定 ``replace(tzinfo=UTC)`` 补
      后缀，**墙钟数字不变**——绝不可走 astimezone（naive 调 astimezone
      会按本地时区 Asia/Shanghai 解释平移 +8h）；
    - aware：``astimezone(UTC)`` 折算（时刻不变、墙钟变 UTC 读数）；
    - ``timespec="microseconds"`` 恒全精度——Python isoformat 在
      microsecond==0 时省略小数段，不显式给会分裂出「有时无分数」形态；
    - None → ``""``（nullable 列出口惯例，与既有三元站点同形）。
    """
    if dt is None:
        return ""
    normalized = ensure_aware(dt)
    return normalized.isoformat(timespec="microseconds")


def local_day_window_utc(day: date | None = None) -> tuple[datetime, datetime]:
    """本地日历日 → (start_utc, end_utc)，aware UTC。

    先把本地日界换算成 UTC，再与库值比较（避免 Chronos 时区类 bug）。
    UTCDateTime 要求查询绑定参数 aware；共享入口直接产出 aware UTC，
    查询窗与历史 SQLite 的 UTC 文本保持同一时刻。数据库落盘仍使用
    无偏移 UTC 文本，ORM 读回则带 UTC 时区。

    票 32 起为读侧本地日语义的共享真源：qijuzhu 起居注/usage 每日桶
    直调本函数；票 84 起 digest_worker 的兼容入口也委托本函数。
    本地指服务进程的宿主时区；此处不决定独立配置的 UTC cron 触发点。
    """
    local_now = datetime.now().astimezone()
    if day is None:
        day = local_now.date()
    local_start = datetime.combine(day, time.min, tzinfo=local_now.tzinfo)
    local_end = local_start + timedelta(days=1)
    return (local_start.astimezone(UTC), local_end.astimezone(UTC))


# 注入帧时间渲染的粒度集（票据 .scratch/inject-frame-time/01，上游 f0.1+ 5cee73e 同款）。
# 顺序即语义的常量此处只有粒度枚举：渲染优先级（event_time → valid_from）在
# render_event_time 的 docstring 里钉死，改顺序必须先改测试（双轴语义会被静默颠倒）。
INJECT_DATE_GRANULARITIES = ("day", "minute", "off")

_INJECT_DATE_FORMATS = {
    "day": "%Y-%m-%d",
    "minute": "%Y-%m-%d %H:%M",
}

# 注入帧显示时区（巡检 2026-11-04 改进：库里一律存 UTC，但给模型看的日期要按
# 使用者的墙上时钟——北京时间 0:00–8:00 写的记忆按 UTC 渲染会落到前一天，
# minute 粒度则整体差 8 小时）。只影响「显示」，不改存储与任何时间计算。
DEFAULT_INJECT_TZ = "Asia/Shanghai"

# Windows 的 Python 不自带 IANA 时区库（zoneinfo 要靠 tzdata 包）。下列时区自
# 1991 年起无夏令时，取不到 tzdata 时退回固定偏移，结果与 zoneinfo 一致。
_FIXED_OFFSET_FALLBACK_HOURS = {
    "Asia/Shanghai": 8,
    "Asia/Chongqing": 8,
    "Asia/Harbin": 8,
    "Asia/Urumqi": 6,
    "PRC": 8,
    "Asia/Hong_Kong": 8,
    "Asia/Macau": 8,
    "Asia/Taipei": 8,
    "Asia/Singapore": 8,
    "Asia/Tokyo": 9,
    "Asia/Seoul": 9,
}

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


@lru_cache(maxsize=32)
def resolve_display_tz(name: str | None) -> tzinfo | None:
    """把配置里的时区名解析成 tzinfo（只用于注入帧显示）。

    认的写法：IANA 名（``Asia/Shanghai``）| ``UTC`` | ``local``（本机系统时区，
    返回 None，调用方 ``astimezone(None)``）| 固定偏移（``+08:00`` / ``UTC+8``）。
    空值、写错值都回落 ``DEFAULT_INJECT_TZ``，并只告警一次（lru_cache）——
    与粒度写错回落 day 同一裁决：不因一个拼写错误让注入时间整段失真或抛异常。
    """
    raw = (name or "").strip()
    if not raw:
        raw = DEFAULT_INJECT_TZ
    if raw.lower() == "local":
        return None
    if raw.upper() in ("UTC", "Z", "GMT"):
        return UTC
    m = _OFFSET_RE.match(raw)
    if m:
        sign, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if hh <= 14 and mm < 60:
            delta = timedelta(hours=hh, minutes=mm)
            return timezone(-delta if sign == "-" else delta)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(raw)
    except Exception:  # ZoneInfoNotFoundError / ValueError（非法名）/ 无 tzdata
        pass
    if raw in _FIXED_OFFSET_FALLBACK_HOURS:
        return timezone(timedelta(hours=_FIXED_OFFSET_FALLBACK_HOURS[raw]))
    if raw != DEFAULT_INJECT_TZ:
        logger.warning("SHELL_HOOK_INJECT_TZ=%r 无法识别，注入时间回落 %s", raw, DEFAULT_INJECT_TZ)
        return resolve_display_tz(DEFAULT_INJECT_TZ)
    return UTC  # 理论不可达：默认时区已在固定偏移表里


def render_event_time(item, granularity: str = "day", tz: str | None = None) -> str:
    """注入帧的时间段渲染：优先事件轴，回落主张轴，取不到不硬造。

    双轴语义（更漏 ADR-0048）：
    - ``event_time``（事件轴）回答「现实何时发生/何时为真」——注入帧回答
      「什么时候」应优先取它；
    - ``valid_from``（主张轴起点，迁移已回填 created_at）是 event_time 为 NULL
      时的语义兜底——「兰台何时知道」，弱于事件轴但强于空白。

    调换这两者的优先级会静默颠倒注入时间轴的含义（且不报错）——
    tests/test_inject_frame_time.py 的 M1 变异体钉死此顺序。

    granularity：``day``（%Y-%m-%d）| ``minute``（%Y-%m-%d %H:%M）| ``off``（不带）。
    写错值/None 回落 day——不因一个拼写错误把时间整段丢掉（上游同款裁决）。

    tz：显示时区（配置 SHELL_HOOK_INJECT_TZ），None/空/写错回落
    ``DEFAULT_INJECT_TZ``（Asia/Shanghai）。存储是 UTC，这里只换算显示——
    北京时间 07:30 的记忆显示为当天，而不是 UTC 的前一天 23:30。

    item：MemoryItem 或含同名字段的 dict；datetime 与 ISO 字符串都认
    （SQLModel 跨版本读回形态不同），解析失败返回空串而非抛异常——
    注入是读线末端，任何脏数据不得打断召回。

    返回空串 = 该条不带时间段（调用方按「无时间」形态渲染）。
    """
    if granularity not in INJECT_DATE_GRANULARITIES:
        granularity = "day"
    if granularity == "off":
        return ""

    value = None
    if isinstance(item, dict):
        value = item.get("event_time") or item.get("valid_from")
    else:
        value = getattr(item, "event_time", None) or getattr(item, "valid_from", None)
    if not value:
        return ""

    if isinstance(value, str):
        try:
            value = parse_iso_utc(value)
        except ValueError:
            return ""

    fmt = _INJECT_DATE_FORMATS[granularity]
    try:
        return ensure_aware(value).astimezone(resolve_display_tz(tz)).strftime(fmt)
    except (ValueError, AttributeError, OverflowError, OSError):
        return ""
