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
