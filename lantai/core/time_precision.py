"""更漏（ADR-0048）写入侧时间精度纪律（票 08）。

I1 不变式（spec §2.5）：event_time 非空 ⇔ precision 非空枚举值；
显式时间提取只认确定性格式（ISO / 中文年月日 / 年份），匹配不到一律
(None, "")——宁 miss 不脏写，绝不猜测回填（中文相对时间解析器另票）。
"""

import re
from datetime import UTC, datetime

PRECISION_ENUM = {"year", "month", "day", "hour", "minute", "second", "fuzzy"}

# 显式时间格式（确定性正则；未来挂相对时间解析器时在此扩展并单独评测）
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CN_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_CN_YEAR_RE = re.compile(r"(\d{4})年")


def validate_event_time_pair(event_time, precision) -> bool:
    """I1：event_time 非空 ⇔ precision ∈ 枚举；为空 ⇒ precision 必须为空串。"""
    if event_time is None:
        return precision == ""
    return precision in PRECISION_ENUM


def extract_explicit_event_time(text: str) -> tuple:
    """从文本提取显式事件时间；返回 (datetime | None, precision)。

    确定性格式优先级：完整日期（ISO/中文）→ 仅年份。匹配不到 → (None, "")。

    aware UTC（`tzinfo=UTC`）：文本里的日期无时区，按 UTC 解释。带时区是因为
    sqlmodel ≥0.0.47 的 UTCDateTime 拒绝 naive datetime 写库——提取结果经
    `.isoformat()` 落 provenance，再由 promoter `fromisoformat` 解析后写入
    `MemoryItem.event_time`，naive 值会在那一跳触发 ValueError。时刻数值不变
    ——2026-09-15 仍是 2026-09-15 00:00:00 UTC。
    """
    text = text or ""
    m = _ISO_DATE_RE.search(text) or _CN_DATE_RE.search(text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC)
        except ValueError:
            return None, ""
        return dt, "day"
    m = _CN_YEAR_RE.search(text)
    if m:
        return datetime(int(m.group(1)), 1, 1, tzinfo=UTC), "year"
    return None, ""
