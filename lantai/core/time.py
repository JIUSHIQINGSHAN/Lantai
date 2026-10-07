from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime | None) -> datetime | None:
    """补时区：naive 按 UTC 解释，aware 归一为 UTC；None 原样返回。

    sqlmodel ≥0.0.47 的 `UTCDateTime` 对 naive datetime 写库直接 raise
    （`process_bind_param` 强制 `utcoffset() is not None`），产品链路凡是要落到
    datetime 列的值（含从字符串/provenance 解析回来的）都须过这道。
    时刻数值不变——只补 tzinfo / 换算到 UTC，不改墙上时钟读数。
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
    要写回 datetime 列的场景——naive 结果会被 `UTCDateTime` 拒写。
    """
    text = value.strip()
    if not text:
        raise ValueError("empty ISO timestamp")
    return ensure_aware(datetime.fromisoformat(text.replace("Z", "+00:00")))


# 注入帧时间渲染的粒度集（票据 .scratch/inject-frame-time/01，上游 f0.1+ 5cee73e 同款）。
# 顺序即语义的常量此处只有粒度枚举：渲染优先级（event_time → valid_from）在
# render_event_time 的 docstring 里钉死，改顺序必须先改测试（双轴语义会被静默颠倒）。
INJECT_DATE_GRANULARITIES = ("day", "minute", "off")

_INJECT_DATE_FORMATS = {
    "day": "%Y-%m-%d",
    "minute": "%Y-%m-%d %H:%M",
}


def render_event_time(item, granularity: str = "day") -> str:
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
        return ensure_aware(value).strftime(fmt)
    except (ValueError, AttributeError, OverflowError):
        return ""
