"""注入帧带时间（票据 .scratch/inject-frame-time/issues/01）。

不 mock 冒烟：真实 MemoryItem 字段 + 真 build_context（内存 SQLite）。
上游 f0.1 教训镜像：存得对、搜得到，就是没给模型看——注入那一刻丢时间。
"""

import importlib.util
import io
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)

# ---------------------------------------------------------------- 纯函数矩阵


def test_render_event_time_prefers_event_time_over_valid_from():
    """双轴优先级：event_time 非空时压过 valid_from（事件轴回答「现实何时」）。"""
    from lantai.core.time import render_event_time

    item = {
        "event_time": datetime(2026, 9, 20, 14, 30, tzinfo=UTC),
        "valid_from": datetime(2026, 10, 1, tzinfo=UTC),
    }
    assert render_event_time(item, "day") == "2026-09-20"
    assert render_event_time(item, "minute") == "2026-09-20 14:30"


def test_render_event_time_falls_back_to_valid_from():
    """event_time NULL → 回落 valid_from（迁移已回填，语义兜底）。"""
    from lantai.core.time import render_event_time

    item = {"event_time": None, "valid_from": datetime(2026, 10, 1, tzinfo=UTC)}
    assert render_event_time(item, "day") == "2026-10-01"


def test_render_event_time_both_missing_returns_empty():
    """两轴全空 → 空串（注入行不带时间段，宁 miss 不硬造）。"""
    from lantai.core.time import render_event_time

    assert render_event_time({"event_time": None, "valid_from": None}, "day") == ""
    assert render_event_time({}, "day") == ""


def test_render_event_time_off_granularity_returns_empty():
    """off 粒度：有时间也不渲染（使用者偏好）。"""
    from lantai.core.time import render_event_time

    item = {"event_time": datetime(2026, 9, 20, tzinfo=UTC)}
    assert render_event_time(item, "off") == ""


def test_render_event_time_bad_granularity_falls_back_to_day():
    """粒度写错值回落 day——不因拼写错误把时间整段丢掉（上游同款）。"""
    from lantai.core.time import render_event_time

    item = {"event_time": datetime(2026, 9, 20, 14, 30, tzinfo=UTC)}
    assert render_event_time(item, "weekly") == "2026-09-20"
    assert render_event_time(item, "") == "2026-09-20"
    assert render_event_time(item, None) == "2026-09-20"


def test_render_event_time_accepts_iso_strings():
    """MemoryItem 行经 SQLModel 读回可能是字符串形态（跨版本 tzinfo 漂移教训）：
    ISO 串输入与 datetime 输入同结果。"""
    from lantai.core.time import render_event_time

    item = {"event_time": "2026-09-20T14:30:00+00:00", "valid_from": None}
    assert render_event_time(item, "minute") == "2026-09-20 14:30"
    # 非 ISO 垃圾串：不硬造，返回空
    assert render_event_time({"event_time": "not-a-date", "valid_from": None}, "day") == ""


# ---------------------------------------------------------------- shell_hook 集成


def _load_hook():
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_engine(rows, null_time_ids=()):
    """建内存引擎并插入记忆行。

    null_time_ids：把这些 id 的 valid_from/event_time 用 raw UPDATE 置 NULL。
    直接 MemoryItem(valid_from=None) 不行——SQLModel 的 default_factory 在
    flush 时连显式 None 都覆盖（票 09 已知限制同源）；真实库的 NULL 只存在于
    迁移前老行，须走 raw SQL 才模拟得出。
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        for row in rows:
            s.add(MemoryItem(**row))
        s.commit()
    if null_time_ids:
        from sqlmodel import text

        with engine.begin() as c:
            for mid in null_time_ids:
                c.execute(
                    text("update memoryitem set valid_from=NULL, event_time=NULL where id=:i"),
                    {"i": mid},
                )
    return engine


def _fake_store(ids):
    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": i, "distance": 0.1} for i in ids]

    return _FakeStore()


def test_shell_hook_inject_line_carries_event_time():
    """有 event_time 的记忆：注入行带 [日期 | score] 前缀。"""
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    et = datetime.now(UTC) - timedelta(days=3)
    engine = _seed_engine(
        [
            {
                "id": "mem_t",
                "memory_type": "semantic",
                "key": "k",
                "content": "用户在九月去了一趟成都",
                "event_time": et,
            }
        ]
    )
    with (
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch.object(mod, "get_vector_store", lambda: _fake_store(["mem_t"])),
    ):
        out = mod.build_context("这是一个超过三字符的查询")

    ctx = out["context"]
    assert "mem_t" in ctx
    # 时间段落在行内：[2026-09-XX | score 0.9]
    day = et.strftime("%Y-%m-%d")
    assert day in ctx, f"注入行缺事件时间 {day}: {ctx[:300]}"
    assert "| score" in ctx


def test_shell_hook_no_time_line_unchanged():
    """无任何时间字段（event_time/valid_from 全 NULL）的旧行为逐字不变。"""
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    engine = _seed_engine(
        [
            {
                "id": "mem_n",
                "memory_type": "semantic",
                "key": "k",
                "content": "一条没有时间出身的旧记忆",
                "event_time": None,
                "valid_from": None,
            }
        ],
        null_time_ids=[
            "mem_n"
        ],  # raw UPDATE 置 NULL：模拟迁移前老行（default_factory 会覆盖显式 None）
    )
    with (
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch.object(mod, "get_vector_store", lambda: _fake_store(["mem_n"])),
    ):
        out = mod.build_context("这是一个超过三字符的查询")

    # 输出行被樊篱包成 <memory_data>…</memory_data>（P0 票03），判据先剥标签
    stripped = [
        ln.replace("<memory_data>", "").replace("</memory_data>", "")
        for ln in out["context"].splitlines()
        if "没有时间出身" in ln
    ]
    line = next(ln for ln in stripped if ln.startswith("- ["))
    assert "| score" not in line, f"无时间却渲染了时间段: {line}"
    assert line == "- [0.9] 一条没有时间出身的旧记忆", f"旧行为漂移: {line}"


def test_shell_hook_granularity_off_hides_time():
    """SHELL_HOOK_INJECT_DATE=off：有时间也不渲染（使用者在 .env 关掉）。"""
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    et = datetime.now(UTC) - timedelta(days=1)
    engine = _seed_engine(
        [
            {
                "id": "mem_o",
                "memory_type": "semantic",
                "key": "k",
                "content": "带时间但被关掉的记忆",
                "event_time": et,
            }
        ]
    )
    with (
        patch.object(mod.settings, "SHELL_HOOK_INJECT_DATE", "off"),
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch.object(mod, "get_vector_store", lambda: _fake_store(["mem_o"])),
    ):
        out = mod.build_context("这是一个超过三字符的查询")

    stripped = [
        ln.replace("<memory_data>", "").replace("</memory_data>", "")
        for ln in out["context"].splitlines()
        if "带时间但被关掉" in ln
    ]
    line = next(ln for ln in stripped if ln.startswith("- ["))
    assert "| score" not in line, f"off 粒度未生效: {line}"


def test_shell_hook_minute_granularity():
    """minute 粒度渲染到分（区分同一天内先后）。"""
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    et = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    engine = _seed_engine(
        [
            {
                "id": "mem_m",
                "memory_type": "semantic",
                "key": "k",
                "content": "同一天内先后的记忆",
                "event_time": et,
            }
        ]
    )
    with (
        patch.object(mod.settings, "SHELL_HOOK_INJECT_DATE", "minute"),
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch.object(mod, "get_vector_store", lambda: _fake_store(["mem_m"])),
    ):
        out = mod.build_context("这是一个超过三字符的查询")

    assert "2026-09-20 14:30" in out["context"]


# ---------------------------------------------------------------- cognitive context


def test_cognitive_context_record_and_prompt_carry_time():
    """builder record 带 ISO 事件时间；to_prompt 行尾带 _(at: …)_。"""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.cognition.context import CognitiveContextBuilder
    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    et = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="cog_1",
                memory_type="semantic",
                key="k",
                content="认知上下文的时间可见性",
                role="observation",
                event_time=et,
            )
        )
        s.commit()

    with patch.object(
        __import__("lantai.core.settings", fromlist=["settings"]).settings,
        "SHELL_HOOK_INJECT_DATE",
        "day",
    ):
        with Session(engine) as s:
            builder = CognitiveContextBuilder(s, principal=None)
            ctx = builder.build(task="什么时间去成都")

    assert ctx.facts, "记忆应分流进 facts"
    rec = ctx.facts[0]
    assert rec.get("event_time") == "2026-09-20", f"record 缺时间: {rec}"

    prompt = ctx.to_prompt()
    assert "_(at: 2026-09-20)_" in prompt, f"to_prompt 行缺时间: {prompt[:400]}"


# ---------------------------------------------------------------- offload


def test_offload_inject_summary_carries_time():
    """offload 摘要行与 shell_hook 注入行同形态（单一真源渲染）。

    摘要（evidence）是截断正文不含时间头；时间只落在注入块行首。
    """
    from lantai.core.time import render_event_time
    from lantai.services.offload_service import build_offload_inject

    item = {"event_time": datetime(2026, 9, 20, tzinfo=UTC), "valid_from": None}
    t = render_event_time(item, "day")
    block, summary = build_offload_inject(
        "很长的记忆内容" * 300,
        0.87,
        100,
        "…（已卸载全文）",
        "/tmp/offload/mem_x.md",
        time_str=t,
    )
    assert block.startswith(f"- [{t} | score 0.87] ")
    assert summary == "很长的记忆内容" * 100 + "…（已卸载全文）"[:0] or summary.endswith(
        "…（已卸载全文）"
    )
    assert t not in summary  # 摘要不带时间头


def test_offload_inject_without_time_unchanged():
    """无时间：offload 行为与修前逐字一致。"""
    from lantai.services.offload_service import build_offload_inject

    block, summary = build_offload_inject(
        "很长的记忆内容" * 300, 0.87, 100, "…（已卸载全文）", "/tmp/offload/mem_x.md"
    )
    assert block.startswith("- [0.87] ")
    assert summary.endswith("…（已卸载全文）")
