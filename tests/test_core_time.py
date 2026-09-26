"""lantai/core/time.py 时间助手冒烟测试（不 mock，纯函数直调）。

覆盖 utcnow / ensure_aware / parse_iso_utc 的主路径与边界：
aware 归一、naive 按 UTC 解释、None 透传、非法 ISO 抛错。

这些助手是 datetime 列写库的唯一时区守门员（sqlmodel ≥0.0.47 的 UTCDateTime
拒绝 naive datetime，见 .scratch/naive-datetime-gate/issues/01-naive-datetime.md），
产品链路（import_service / promoter / record_ops_service）写库前都要过它们。
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from lantai.core.time import ensure_aware, parse_iso_utc, utcnow


class TestUtcnow:
    def test_returns_aware_utc(self):
        now = utcnow()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)
        # 与「现在」足够近（跨日/慢机器也不会差过一分钟）
        assert abs((datetime.now(UTC) - now).total_seconds()) < 60


class TestEnsureAware:
    def test_none_passthrough(self):
        assert ensure_aware(None) is None

    def test_naive_is_interpreted_as_utc(self):
        """naive 按 UTC 解释：补 tzinfo，不改时刻数值。"""
        naive = datetime(2026, 9, 15, 8, 30, 0)
        aware = ensure_aware(naive)
        assert aware.tzinfo is not None
        assert aware.utcoffset() == timedelta(0)
        assert aware.replace(tzinfo=None) == naive  # 墙上时钟读数不变

    def test_aware_is_normalized_to_utc(self):
        """+08:00 输入换算为 UTC：同一时刻，读数相应平移。"""
        plus8 = timezone(timedelta(hours=8))
        local = datetime(2026, 9, 15, 16, 30, 0, tzinfo=plus8)
        aware = ensure_aware(local)
        assert aware.utcoffset() == timedelta(0)
        assert aware == datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)
        assert aware == local  # 同一时刻（跨时区相等）

    def test_already_utc_is_identity(self):
        original = datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)
        assert ensure_aware(original) == original


class TestParseIsoUtc:
    def test_offset_is_converted_to_utc(self):
        """带偏移的串换算到 UTC（时刻不变）。"""
        parsed = parse_iso_utc("2026-09-15T16:30:00+08:00")
        assert parsed == datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)

    def test_z_suffix_is_utc(self):
        parsed = parse_iso_utc("2026-09-15T08:30:00Z")
        assert parsed == datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)

    def test_naive_string_is_interpreted_as_utc(self):
        """无偏移串按 UTC 解释（导入/纠错链约定：全部时间戳归一到 UTC）。"""
        parsed = parse_iso_utc("2026-09-15T08:30:00")
        assert parsed == datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)
        assert parsed.utcoffset() == timedelta(0)

    def test_surrounding_whitespace_tolerated(self):
        assert parse_iso_utc("  2026-09-15T08:30:00Z  ") == datetime(
            2026, 9, 15, 8, 30, 0, tzinfo=UTC
        )

    def test_result_is_always_writable_to_datetime_column(self):
        """写库守门：任何合法输入的结果都带 tzinfo（UTCDateTime 硬要求）。"""
        for text in ("2026-09-15T08:30:00", "2026-09-15T08:30:00Z", "2026-09-15T16:30:00+08:00"):
            assert parse_iso_utc(text).tzinfo is not None

    @pytest.mark.parametrize("bad", ["", "   ", "not-a-time", "2026-13-45"])
    def test_invalid_raises_value_error(self, bad):
        with pytest.raises(ValueError):
            parse_iso_utc(bad)
