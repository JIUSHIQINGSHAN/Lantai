"""票 67(会话/事务卫生家族)红绿验收——三引擎裸 Session 泄漏 + auto_apply 整体提交调用方会话 + TOCTOU。

测试纪律(AGENTS.md):全部真库直调,不 mock 被测链路。

- 泄漏臂:真 QueuePool 文件库上连续调三引擎的自建会话路径(不注入 session),
  用 ``pool.checkedout()`` 实证连接是否随调用归还。修复前滞留的机理已实证
  (``.scratch/v0.68-test-honesty/tools/_probe67_pool_gc.py``):未 close 的
  Session 因 Session↔Transaction↔Connection 循环引用,refcount 死亡不还连接,
  第 16 次调用即 QueuePool(5+10) 耗尽 → TimeoutError——票面预言的故障形态。
- 保存点臂:调用方会话持未提交行时,auto_apply 不得整体提交调用方事务。
- TOCTOU 臂:提名条件(unhelpful 的计数判定)在 Python 侧读会话对象,借调用方
  会话身份映射的滞留读打开 check/use 窗——第二会话把前提(helpful_count=0)
  真写推翻后,落库前不重验就会按滞留计数降权。修复 = 重取证(refresh)后重验
  提名条件,条件不再成立则跳过(宁 miss 不脏写)。status/retract 变体只在
  自有会话路径的「快照会话已关、落库会话新开」缝隙里出现(提名 SQL 过滤
  status,调用方路径骗不过 WHERE),与本臂同一处重验收口。
- 回归臂:自有会话路径(self-owned)的 auto_apply 仍必须真实落库、卷宗自建
  会话路径的写读不回归。
"""

from __future__ import annotations

import gc
from contextlib import contextmanager

import pytest
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition.canshang import CanshangEngine
from lantai.cognition.juanzong import JuanzongEngine
from lantai.cognition.qijuzhu import QijuzhuEngine
from lantai.cognition.sanxing import SanxingEngine
from lantai.models.tables import Dossier, MemoryItem


@pytest.fixture
def real_engine(tmp_path):
    """真文件库 + 默认 QueuePool(5+10):连接池语义可观测,多会话真隔离。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'ticket67.db'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    gc.collect()
    engine.dispose()


@contextmanager
def _db_engine_swap(engine):
    """把 db.engine 临时指到被测真库(仓内既有口径),用毕必还(绊线盯未还原)。"""
    import lantai.storage.db as db_module

    orig = db_module.engine
    db_module.engine = engine
    try:
        yield
    finally:
        db_module.engine = orig


def _seed_memory(engine: Session, **kwargs) -> None:
    with Session(engine) as s:
        s.add(MemoryItem(status="active", confidence=0.9, **kwargs))
        s.commit()


# ── 臂 1:泄漏红臂(三引擎自建会话必须用毕即还)──────────────────────


def test_engine_self_owned_sessions_release_connections(real_engine):
    """连续 10 次自建会话调用后,池内不得有滞留连接。

    10 < QueuePool(5+10)=15:修复前红(checkedout 大于 0)且留足余量不撞
    30s 耗尽墙;修复后 0。调用期间挂起 gc——未 close 的 Session 因循环引用
    只能等 gc 兜底回收,挂起后红态确定性(每调必漏),修复后的显式 close
    不依赖 gc、照常为 0。同时每步断言功能结果,兼作真冒烟。
    """
    with _db_engine_swap(real_engine):
        gc.disable()
        try:
            for _ in range(2):
                assert JuanzongEngine().list_dossiers() == []
                assert JuanzongEngine().get_dossier("dos-none") is None
                report = CanshangEngine().audit_consistency(memory_ids=[])
                assert report.violation_count == 0
                verdict = CanshangEngine().arbitrate_deep_conflict(["mem-none"])
                assert verdict.dominant_memory_id is None
                chronicle = QijuzhuEngine().get_daily_chronicle()
                assert chronicle["metrics"]["total_events"] == 0

            pool = real_engine.pool
            assert pool.checkedout() == 0, (
                f"自建会话调用归还后连接池仍有 {pool.checkedout()} 条滞留连接"
                f"(状态: {pool.status()!r})——裸 Session 未关闭"
            )
        finally:
            gc.enable()


# ── 臂 2:保存点红臂(auto_apply 不得整体提交调用方事务)──────────────


def test_auto_apply_does_not_commit_caller_pending_row(real_engine):
    """调用方会话持未提交行时,auto_apply 只落自己的保存点,不整体提交外层事务。"""
    _seed_memory(
        real_engine,
        id="mem_sup_01",
        content="老版认证接口规范",
        structure={"superseded_by": "mem_sup_new"},
    )

    with Session(real_engine) as caller:
        pending = MemoryItem(id="mem_pending", content="调用方未提交行", status="active")
        caller.add(pending)
        caller.flush()  # 已入事务未提交

        res = SanxingEngine(session=caller).generate_remedies(auto_apply=True)
        assert res.applied_count == 1  # 自愈本体确实执行了

        # 观察者会话:未提交行不得被引擎代为提交(修复前整体 commit = 红)
        with Session(real_engine) as observer:
            assert observer.get(MemoryItem, "mem_pending") is None

        # 保存点已释放:自愈结果在调用方事务内可见
        assert caller.get(MemoryItem, "mem_sup_01").status == "archived"
        caller.commit()

    # 调用方自己提交后,两行都应持久
    with Session(real_engine) as observer:
        assert observer.get(MemoryItem, "mem_pending") is not None
        assert observer.get(MemoryItem, "mem_sup_01").status == "archived"


# ── 臂 3:TOCTOU 红臂(重取证后条件不再成立 → 跳过,宁 miss 不脏写)──────


def test_auto_apply_skips_candidate_whose_premise_changed_after_nomination(real_engine):
    """提名后、落库前提名前提被第二会话推翻:auto_apply 不得按滞留计数降权。

    unhelpful 的提名判定(use_count>=5 且 helpful_count==0)在 Python 侧读
    会话对象——调用方身份映射持有滞留读时,落库前不重验就会把「已有帮助」
    的记忆按旧计数降权(票面 TOCTOU:提交时不再重验)。
    """
    _seed_memory(
        real_engine,
        id="mem_unh_01",
        content="高频零帮助碎片",
        use_count=10,
        helpful_count=0,
        importance=0.8,
    )

    with Session(real_engine) as caller:
        # 先载入调用方会话身份映射,再由第二会话真写推翻前提
        loaded = caller.get(MemoryItem, "mem_unh_01")
        assert loaded.helpful_count == 0

        with Session(real_engine) as helper:
            row = helper.get(MemoryItem, "mem_unh_01")
            row.helpful_count = 1
            helper.commit()

        res = SanxingEngine(session=caller).generate_remedies(auto_apply=True)
        assert res.applied_count == 0  # 修复前:按滞留计数照常降权 = 红
        assert res.remedies[0].executed is False

    with Session(real_engine) as observer:
        row = observer.get(MemoryItem, "mem_unh_01")
        assert row.helpful_count == 1
        assert row.importance != 0.1  # 降权不得落库(修复前 = 0.1 = 红)


# ── 臂 4:回归臂(自有会话路径的落库语义不回归)──────────────────────


def test_sanxing_self_owned_session_still_persists(real_engine):
    """自有会话路径(db.engine)的 auto_apply 仍必须外层提交真实落库——收口不许把生产默认路径改哑。"""
    _seed_memory(
        real_engine,
        id="mem_owned",
        content="自有会话路径自愈对象",
        structure={"superseded_by": "mem_succ"},
    )

    with _db_engine_swap(real_engine):
        res = SanxingEngine(principal=None).generate_remedies(auto_apply=True)
        assert res.applied_count == 1

    with Session(real_engine) as observer:
        assert observer.get(MemoryItem, "mem_owned").status == "archived"


def test_juanzong_self_owned_session_persists_and_detached_read(real_engine):
    """卷宗自建会话路径:写入持久 + 会话关闭后返回对象属性仍可读(序列化面不回归)。"""
    with _db_engine_swap(real_engine):
        dossier = JuanzongEngine().create_dossier(
            title="票 67 回归案卷",
            goal="验证自建会话关闭后对象仍可序列化读取",
            milestones=[{"id": "ms-1", "title": "起案", "status": "done"}],
        )
        assert dossier.id.startswith("dos_")
        assert dossier.title == "票 67 回归案卷"  # 会话已还,属性须已加载

        found = JuanzongEngine().get_dossier(dossier.id)
        assert found is not None
        assert found.goal.startswith("验证自建会话")

    with Session(real_engine) as observer:
        row = observer.get(Dossier, dossier.id)
        assert row is not None
        assert row.milestones[0]["status"] == "done"
