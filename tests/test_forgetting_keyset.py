"""票 09（.scratch/review-fixes-2026-10/issues/09）：forgetting keyset 分页。

`apply_forgetting` 此前 `s.exec(q).all()` 一次性全表加载（审查 M9-4），库到数万条后
内存与耗时随库容线性放大。本票改为按 id 序 keyset 分批（批大小可注入）。

硬约束：判定逻辑逐字不动；同一库状态下遗忘结果与全量加载口径一致。

三个冒烟全部不 mock 被测函数内部逻辑：真实 SQLite 内存库直调 `apply_forgetting`；
SQL 形态用 `before_cursor_execute` 事件监听**观测**（只读不改，不替换任何计算）；
时钟用真实 `utcnow`，靠把 `last_used_at` 放到过去构造天数差。
"""

import math
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.memory.forgetting import apply_forgetting
from lantai.models.tables import MemoryItem

# 注入的小批大小：28 条种子 → 3 批，跨多批（条数 > 2× 批大小）
PAGE_SIZE = 10


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _strength() -> float:
    """general lane、importance=0.5、use_count=0 的保持强度（对齐 _lane_strength）。"""
    profile = settings.LANE_DECAY_PROFILES["general"]
    return profile["base_s"] + profile["importance_boost"] * 0.5


def _seed(engine, base):
    """28 条种子，覆盖三类语义：衰减更新 ×24 + 到期归档 ×2 + 不动 ×2。

    id 取零填充 ASCII，字典序即遍历序，特殊条目分散落进不同批次边界。
    """
    with Session(engine) as s:
        for i in range(24):
            s.add(
                MemoryItem(
                    id=f"ks-f{i:02d}",
                    content=f"填充记忆{i}",
                    memory_type="text",
                    tier="longterm",
                    lane="general",
                    importance=0.5,
                    use_count=0,
                    status="active",
                    decay_score=1.0,
                    last_used_at=base - timedelta(days=1),
                    created_at=base - timedelta(days=1),
                )
            )
        s.add(
            MemoryItem(
                id="ks-stale",
                content="陈年事实（decay 应跌破阈值归档）",
                memory_type="text",
                tier="longterm",
                lane="general",
                importance=0.5,
                status="active",
                decay_score=1.0,
                last_used_at=base - timedelta(days=400),
                created_at=base - timedelta(days=400),
            )
        )
        ttl_days = settings.WORKING_MEMORY_TTL_DAYS * 1.2
        s.add(
            MemoryItem(
                id="ks-work",
                content="过期工作项（TTL 分支归档）",
                memory_type="text",
                tier="working",
                lane="general",
                importance=0.5,
                helpful_count=0,
                status="active",
                decay_score=1.0,
                last_used_at=base - timedelta(days=ttl_days),
                created_at=base - timedelta(days=ttl_days),
            )
        )
        s.add(
            MemoryItem(
                id="ks-proc",
                content="程序性技能（铁律不动）",
                memory_type="text",
                decay_class="procedural",
                tier="longterm",
                lane="general",
                importance=0.5,
                status="active",
                decay_score=1.0,
                last_used_at=base - timedelta(days=9999),
                created_at=base - timedelta(days=9999),
            )
        )
        s.add(
            MemoryItem(
                id="ks-fresh",
                content="刚写入的记忆（|Δdecay|<0.001 不动）",
                memory_type="text",
                tier="longterm",
                lane="general",
                importance=0.5,
                status="active",
                decay_score=1.0,
                last_used_at=base,
                created_at=base,
            )
        )
        s.commit()


def _read_states(engine):
    with Session(engine) as s:
        rows = s.exec(select(MemoryItem).order_by(MemoryItem.id)).all()
        return {r.id: (r.status, r.decay_score) for r in rows}


def _assert_semantics(states):
    """遗忘结果 == 全量加载口径（逐条对齐 forgetting.py 判定语义）。"""
    one_day_decay = math.exp(-1.0 / _strength())
    for i in range(24):
        status, decay = states[f"ks-f{i:02d}"]
        assert status == "active", f"ks-f{i:02d} 不应被归档"
        assert decay == pytest.approx(one_day_decay, rel=1e-3), (
            f"ks-f{i:02d} 应衰减更新到 {one_day_decay}，实得 {decay}"
        )
    # 到期归档·衰减分支：decay 跌破 ARCHIVE_DECAY_THRESHOLD
    status, decay = states["ks-stale"]
    assert status == "archived", "400 天陈旧记忆应被自动归档"
    assert decay < settings.ARCHIVE_DECAY_THRESHOLD
    # 到期归档·working TTL 分支：decay 未跌破阈值，靠 TTL + 零 helpful 归档
    ttl_days = settings.WORKING_MEMORY_TTL_DAYS * 1.2
    ttl_decay = math.exp(-ttl_days / _strength())
    assert ttl_decay >= settings.ARCHIVE_DECAY_THRESHOLD, (
        "测试前提失效：ks-work 应走 TTL 分支而非衰减归档"
    )
    status, decay = states["ks-work"]
    assert status == "archived", "working 超期零帮助记忆应被 TTL 分支归档"
    assert decay == pytest.approx(ttl_decay, rel=1e-2)
    # 未到期不动
    assert states["ks-proc"] == ("active", 1.0), "procedural 铁律：永不衰减、不归档"
    assert states["ks-fresh"] == ("active", 1.0), "刚写入的记忆 |Δdecay|<0.001 不应写库"


@pytest.fixture
def ks_env():
    """内存 SQLite + 真实建表 + patch db.get_session + SQL 观测器（只读不改）。"""
    engine = _make_engine()
    queries: list[tuple[str, tuple]] = []
    recording = {"on": False}

    def on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if not recording["on"]:
            return
        sql = statement.strip().lower()
        if sql.startswith("select") and "memoryitem" in sql:
            queries.append((statement, tuple(parameters) if parameters is not None else ()))

    event.listen(engine, "before_cursor_execute", on_cursor_execute)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield SimpleNamespace(
            engine=engine, sf=session_factory, queries=queries, recording=recording
        )


def test_no_unbounded_full_table_load(ks_env):
    """红：遗忘周期不得一次性全表加载（M9-4 根因），必须全部带 LIMIT。"""
    _seed(ks_env.engine, utcnow())
    ks_env.recording["on"] = True
    apply_forgetting()
    ks_env.recording["on"] = False

    assert ks_env.queries, "apply_forgetting 未执行任何 memoryitem 查询"
    unbounded = [stmt for stmt, _ in ks_env.queries if "limit" not in stmt.lower()]
    assert not unbounded, f"遗忘周期仍存在 {len(unbounded)} 条无界全表加载（应改 keyset 分页）"


def test_page_size_injectable_and_effective(ks_env):
    """红→绿：批大小参数可注入且真实生效（28 条 / 10 = 3 批）。"""
    _seed(ks_env.engine, utcnow())
    ks_env.recording["on"] = True
    apply_forgetting(page_size=PAGE_SIZE)
    ks_env.recording["on"] = False

    paged = [(stmt, params) for stmt, params in ks_env.queries if "limit" in stmt.lower()]
    assert len(paged) == 3, f"28 条、批大小 10 应恰好 3 批，实得 {len(paged)} 批"
    for stmt, params in paged:
        assert PAGE_SIZE in params, f"分页查询未携带批大小：{stmt[:120]} | {params}"

    states = _read_states(ks_env.engine)
    one_day_decay = math.exp(-1.0 / _strength())
    assert states["ks-f00"][1] == pytest.approx(one_day_decay, rel=1e-3), (
        "分批之后遗忘主路径必须照常生效"
    )


def test_results_independent_of_batch_size():
    """硬约束：同一库状态下，多批与单页（≈旧全量加载口径）遗忘结果逐条一致。"""
    base = utcnow()
    engine_batched = _make_engine()
    engine_single = _make_engine()
    _seed(engine_batched, base)
    _seed(engine_single, base)

    with patch.object(db_module, "get_session", lambda: Session(engine_batched)):
        apply_forgetting(page_size=PAGE_SIZE)
    with patch.object(db_module, "get_session", lambda: Session(engine_single)):
        apply_forgetting(page_size=1000)

    states_batched = _read_states(engine_batched)
    states_single = _read_states(engine_single)

    assert set(states_batched) == set(states_single)
    for mid, (status_b, decay_b) in states_batched.items():
        status_s, decay_s = states_single[mid]
        assert status_b == status_s, f"{mid} 处置不一致：{status_s} → {status_b}"
        assert decay_b == pytest.approx(decay_s, rel=1e-4), f"{mid} decay 不一致"

    _assert_semantics(states_batched)
    _assert_semantics(states_single)
