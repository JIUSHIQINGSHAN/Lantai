"""注入帧时间按显示时区渲染（巡检 2026-11-04 改进，SHELL_HOOK_INJECT_TZ）。

病灶：库里时间一律存 UTC，render_event_time 原来直接按 UTC strftime——北京时间
0:00–8:00 写的记忆在注入帧里显示成前一天，minute 粒度整体差 8 小时。模型拿到
「[2026-11-03] 早上复查了血常规」会把今天的事当成昨天。

修法：只换算显示（astimezone），存储与所有时间计算不动；默认 Asia/Shanghai，
可配 UTC（旧行为）、local（本机时区）、固定偏移；写错回落默认、不抛异常。
"""

import importlib.util
import logging
import os
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lantai.core.time import render_event_time, resolve_display_tz

CST = timezone(timedelta(hours=8))
# 北京时间 2026-11-04 07:30 = UTC 2026-11-03 23:30（库里实际存的就是后者）
MORNING_CST = datetime(2026, 11, 4, 7, 30, tzinfo=CST)
MORNING_UTC = MORNING_CST.astimezone(UTC)

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


@pytest.fixture(autouse=True)
def _clear_tz_cache():
    resolve_display_tz.cache_clear()
    yield
    resolve_display_tz.cache_clear()


# ---------------------------------------------------------------- 纯函数


def test_beijing_morning_memory_shows_same_day_by_default():
    """回归：北京 07:30 的记忆默认显示当天，不再是 UTC 的前一天。"""
    item = {"event_time": MORNING_UTC}
    assert render_event_time(item, "day") == "2026-11-04"
    assert render_event_time(item, "minute") == "2026-11-04 07:30"


def test_utc_setting_keeps_old_behavior():
    """SHELL_HOOK_INJECT_TZ=UTC：逐字恢复改动前的渲染。"""
    item = {"event_time": MORNING_UTC}
    assert render_event_time(item, "day", tz="UTC") == "2026-11-03"
    assert render_event_time(item, "minute", tz="UTC") == "2026-11-03 23:30"


def test_iso_string_and_naive_inputs_are_converted():
    """SQLModel 读回的 ISO 串（Z 后缀）与 naive（按 UTC 解释）同样换算到显示时区。"""
    assert render_event_time({"event_time": "2026-11-03T23:30:00Z"}, "minute") == "2026-11-04 07:30"
    naive = datetime(2026, 11, 3, 23, 30)
    assert render_event_time({"event_time": naive}, "minute") == "2026-11-04 07:30"
    assert render_event_time({"valid_from": naive}, "day") == "2026-11-04"


@pytest.mark.parametrize(
    ("tz", "expected"),
    [
        ("Asia/Shanghai", "2026-11-04 07:30"),
        ("+08:00", "2026-11-04 07:30"),
        ("UTC+8", "2026-11-04 07:30"),
        ("+05:30", "2026-11-04 05:00"),
        ("UTC-3", "2026-11-03 20:30"),
        ("utc", "2026-11-03 23:30"),
        ("", "2026-11-04 07:30"),
        (None, "2026-11-04 07:30"),
    ],
)
def test_tz_spellings(tz, expected):
    assert render_event_time({"event_time": MORNING_UTC}, "minute", tz=tz) == expected


def test_bad_tz_falls_back_to_default_without_raising(caplog):
    """写错时区名：回落北京时间并告警，绝不打断召回（注入是读线末端）。"""
    with caplog.at_level(logging.WARNING, logger="lantai.core.time"):
        out = render_event_time({"event_time": MORNING_UTC}, "minute", tz="Mars/Olympus")
    assert out == "2026-11-04 07:30"
    assert any("Mars/Olympus" in r.getMessage() for r in caplog.records)


def test_shanghai_works_without_tzdata():
    """Windows 无 tzdata 时 ZoneInfo 抛错：Asia/Shanghai 退回固定 +08:00，结果不变。"""
    import zoneinfo

    def _boom(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    with patch("zoneinfo.ZoneInfo", _boom):
        resolve_display_tz.cache_clear()
        out = render_event_time({"event_time": MORNING_UTC}, "minute", tz="Asia/Shanghai")
        bad = render_event_time({"event_time": MORNING_UTC}, "minute", tz="Europe/Nowhere")
    assert out == "2026-11-04 07:30"
    assert bad == "2026-11-04 07:30"


def test_local_uses_system_timezone():
    """local：跟随本机系统时区（与 datetime.astimezone() 无参结果逐字一致，跨平台不弃权）。"""
    out = render_event_time({"event_time": MORNING_UTC}, "minute", tz="local")
    assert out == MORNING_UTC.astimezone().strftime("%Y-%m-%d %H:%M")
    assert resolve_display_tz("LOCAL") is None


def test_setting_default_is_beijing():
    from lantai.core.settings import Settings

    assert Settings.model_fields["SHELL_HOOK_INJECT_TZ"].default == "Asia/Shanghai"


# ---------------------------------------------------------------- 端到端：shell_hook 注入帧


def _load_hook():
    spec = importlib.util.spec_from_file_location("shell_hook_tz", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine_with(row):
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(MemoryItem(**row))
        s.commit()
    return engine


def _fake_store(ids):
    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": i, "distance": 0.1} for i in ids]

    return _FakeStore()


@pytest.mark.parametrize(
    ("tz_setting", "expected", "unexpected"),
    [
        ("Asia/Shanghai", "[2026-11-04 | score", "2026-11-03"),
        ("UTC", "[2026-11-03 | score", "2026-11-04"),
    ],
)
def test_shell_hook_frame_uses_display_tz(tz_setting, expected, unexpected):
    """真 build_context：北京早上写的记忆，注入行日期跟着 SHELL_HOOK_INJECT_TZ 走。"""
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    engine = _engine_with(
        {
            "id": "mem_tz",
            "memory_type": "semantic",
            "key": "k",
            "content": "早上去医院复查了血常规",
            "event_time": MORNING_UTC,
        }
    )
    with (
        patch.object(mod.settings, "SHELL_HOOK_INJECT_DATE", "day"),
        patch.object(mod.settings, "SHELL_HOOK_INJECT_TZ", tz_setting),
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch("lantai.retrieval.hybrid.get_vector_store", lambda: _fake_store(["mem_tz"])),
    ):
        out = mod.build_context("我最近一次复查是什么时候")

    ctx = out.get("context", "")
    # 【相关记忆】段的注入行（【本次依据】段是不带时间的 evidence 行）
    line = next((ln for ln in ctx.splitlines() if "血常规" in ln and "| score" in ln), "")
    assert expected in line, f"注入行日期不对: {line!r}"
    assert unexpected not in line


def test_cognitive_context_record_uses_display_tz():
    """认知上下文 record / to_prompt 同走显示时区（单一真源 render_event_time）。"""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.cognition.context import CognitiveContextBuilder
    from lantai.core.settings import settings
    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="cog_tz",
                memory_type="semantic",
                key="k",
                content="早上去医院复查了血常规",
                role="observation",
                event_time=MORNING_UTC,
            )
        )
        s.commit()

    with (
        patch.object(settings, "SHELL_HOOK_INJECT_DATE", "day"),
        patch.object(settings, "SHELL_HOOK_INJECT_TZ", "Asia/Shanghai"),
        Session(engine) as s,
    ):
        ctx = CognitiveContextBuilder(s, principal=None).build(task="复查")

    assert ctx.facts and ctx.facts[0].get("event_time") == "2026-11-04"
    assert "_(at: 2026-11-04)_" in ctx.to_prompt()
