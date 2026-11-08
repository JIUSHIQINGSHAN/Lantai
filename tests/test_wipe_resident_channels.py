"""`_wipe_resident_channels` 夹具健壮性（watch 台账 `ci-db-thread-flakes-20261107`）。

会话级共享测试库是 StaticPool 单连接（`check_same_thread=False`）：前一个用例的
后台线程还在收尾时，teardown 里 wipe 的 `conn.commit()` 可能踩 CPython 并发症——
`SystemError: <built-in method commit of sqlite3.Connection object at 0x...>
returned NULL without setting an exception`（CI 单次实抓两例、重跑即绿，台账判读
同族：同一 sqlite 连接被多线程同时使用）。现状 wipe 无重试，一次 SystemError 即
炸 teardown 整组连坐红——假红窗口，与「测试诚实性」同源（假绿查完，假红也要锁）。

本文件锁三个语义：
1. **瞬时 SystemError 必须被整块重试吸收**（DELETE 无条件幂等，整块重试安全），
   重试后 commit 真实成功——不是吞异常装绿；
2. **重试耗尽仍失败必须放行原异常**——常驻注入面清不掉必须红，不许静默跳过
   （v0.66 票 01 的病不许回来）；重试次数与 conftest 的
   `_WIPE_RETRY_ATTEMPTS`（当前 3）一致，改重试上限须同步改此臂；
3. **真库冒烟**：真会话 engine 上 wipe 真删常驻注入面、不碰别表
   （AGENTS.md 测试纪律：核心函数不 mock 冒烟）。

故障注入说明（如实）：替身只出现在「commit 抛 SystemError」这一处台账已记录的
失效边界上（`sqlite3` factory 子类），连接、磁盘文件、SQL、DELETE 全部真实执行
——不属于 AGENTS.md 禁止的「让被测函数跳过内部计算逻辑」。真实线程竞争无法在
测试里确定性复现（CI 两例均为单次不可重现），故以失效边界注入锁重试语义。
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import tests.conftest as conftest_mod

# 台账同款报错原文（CI job 128300693198 实抓），注入臂原样抛出便于对账。
_SYSTEMERROR_COMMIT = (
    "<built-in method commit of sqlite3.Connection object at 0x...> "
    "returned NULL without setting an exception"
)


def _make_flaky_connection_cls(counter: dict):
    """造一个真实 sqlite3.Connection 子类：布防期间的 commit 抛 SystemError。

    `counter` 为共享计数器（闭包注入，跨实例累加）；其余行为全部真实。
    布防协议：`armed=False` 时 commit 全真（建表/插数据用），测试体在调 wipe
    前置 `armed=True` + `fail_first=N` + `n=0`——这样注入只命中 wipe 自己的
    commit，不会把 create_all 的 DDL commit 也打歪（首轮红测踩过）。
    """

    class _FlakyCommitConnection(sqlite3.Connection):
        def commit(self):
            if counter.get("armed"):
                counter["n"] += 1
                if counter["n"] <= counter["fail_first"]:
                    raise SystemError(_SYSTEMERROR_COMMIT)
            super().commit()

    return _FlakyCommitConnection


def _fresh_engine(tmp_path: Path, flaky_cls) -> object:
    """临时库文件 + StaticPool 单连接 + 注入连接工厂（与会话库同构：单连接共享）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    db_file = tmp_path / "flaky_wipe.db"
    db_file.touch()
    engine = create_engine(
        f"sqlite:///{db_file}",
        creator=lambda: sqlite3.connect(str(db_file), factory=flaky_cls),
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_transient_commit_systemerror_is_retried_and_absorbed(tmp_path, monkeypatch):
    """臂 1：commit 瞬时 SystemError（第 1 次）→ 重试后吸收，且数据真删。"""
    counter = {"n": 0, "armed": False, "fail_first": 0}
    flaky_cls = _make_flaky_connection_cls(counter)
    engine = _fresh_engine(tmp_path, flaky_cls)
    monkeypatch.setattr(conftest_mod, "_SESSION_DB_ENGINE", engine)

    # 先放一行真数据进常驻注入面，wipe 成功后必须真删（不是吞异常装绿）。
    from lantai.models.tables import CoreMemoryBlock

    with Session(engine) as session:
        session.add(CoreMemoryBlock(id="flaky-1", block="identity", content="x"))
        session.commit()

    counter.update(armed=True, fail_first=1, n=0)  # 只对 wipe 的 commit 布防
    try:
        conftest_mod._wipe_resident_channels()  # 现状：SystemError 直接外抛（红）
        assert counter["n"] == 2  # 失败 1 次 + 重试成功 1 次
        with Session(engine) as session:
            assert session.query(CoreMemoryBlock).count() == 0
    finally:
        # 显式撤防并还原：autouse 夹具的后置 wipe 在 monkeypatch 撤销**之前**跑
        # （本轮红测实测），若把布防引擎留给它，teardown 会撞毒 commit、且真实
        # 会话库的常驻注入面在这一例后没人清——两个都不许发生。
        counter["armed"] = False
        monkeypatch.undo()


def test_exhausted_retries_reraise_systemerror(tmp_path, monkeypatch):
    """臂 2：commit 持续 SystemError → 重试满 3 次后放行原异常（宁红不放）。"""
    counter = {"n": 0, "armed": False, "fail_first": 0}
    flaky_cls = _make_flaky_connection_cls(counter)
    engine = _fresh_engine(tmp_path, flaky_cls)
    monkeypatch.setattr(conftest_mod, "_SESSION_DB_ENGINE", engine)

    counter.update(armed=True, fail_first=10, n=0)  # 持续布防，重试救不回
    try:
        with pytest.raises(SystemError, match="returned NULL"):
            conftest_mod._wipe_resident_channels()
        assert counter["n"] == 3  # 与 conftest._WIPE_RETRY_ATTEMPTS 一致（当前 3）
    finally:
        counter["armed"] = False
        monkeypatch.undo()  # 同臂 1：把 teardown 从毒引擎上摘下来


def test_wipe_real_engine_clears_resident_and_spares_others():
    """臂 3：真会话 engine 冒烟——真删常驻注入面，别表不动（不 mock 直调）。"""
    from lantai.models.tables import CoreMemoryBlock, MemoryProposal

    import lantai.storage.db as db_module

    with Session(db_module.engine) as session:
        session.add(
            CoreMemoryBlock(id="wipe-smoke-1", block="identity", content="x")
        )
        session.add(MemoryProposal(id="wipe-smoke-p1", proposal_type="update"))
        session.commit()

    conftest_mod._wipe_resident_channels()

    with Session(db_module.engine) as session:
        assert session.query(CoreMemoryBlock).count() == 0
        assert session.query(MemoryProposal).count() == 1  # 非常驻表不被误伤
