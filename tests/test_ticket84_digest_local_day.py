"""票 84：digest 日窗兼容入口委托共享真源，宿主 UTC/UTC+8 日界行为锁。"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from lantai.core import time as core_time
from lantai.workers import digest_worker


@pytest.mark.parametrize(
    ("offset", "day", "start_iso", "end_iso"),
    [
        (0, None, "2026-11-13T00:00:00+00:00", "2026-11-14T00:00:00+00:00"),
        (8, None, "2026-11-12T16:00:00+00:00", "2026-11-13T16:00:00+00:00"),
        (0, date(2026, 11, 12), "2026-11-12T00:00:00+00:00", "2026-11-13T00:00:00+00:00"),
        (8, date(2026, 11, 12), "2026-11-11T16:00:00+00:00", "2026-11-12T16:00:00+00:00"),
    ],
    ids=["utc-today", "utc8-today", "utc-explicit-day", "utc8-explicit-day"],
)
def test_digest_day_window_delegates_real_shared_calculation(
    monkeypatch, offset, day, start_iso, end_iso
):
    local_tz = timezone(timedelta(hours=offset))

    class LocalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 11, 13, 1, 30, tzinfo=local_tz)
            return value if tz is None else value.astimezone(tz)

        def astimezone(self, tz=None):
            return super().astimezone(local_tz if tz is None else tz)

    calls = []
    shared_window = core_time.local_day_window_utc

    def observe_real_window(day=None):
        calls.append(day)
        return shared_window(day)

    # 只固定时钟输入；见证函数完整委托真实日界计算，不替换输出或跳过逻辑。
    with monkeypatch.context() as scoped:
        scoped.setattr(core_time, "datetime", LocalClock)
        scoped.setattr(core_time, "local_day_window_utc", observe_real_window)
        start, end = digest_worker._local_day_window_utc(day)

    assert calls == [day], "digest 兼容入口必须委托共享日窗，禁止保留复制实现"
    assert start == datetime.fromisoformat(start_iso)
    assert end == datetime.fromisoformat(end_iso)
    assert start.tzinfo is UTC and end.tzinfo is UTC
    assert start.astimezone(local_tz).hour == end.astimezone(local_tz).hour == 0
    assert end - start == timedelta(days=1)
