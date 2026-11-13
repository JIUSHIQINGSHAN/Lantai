"""`_wipe_resident_channels` 夹具健壮性（watch 台账 `ci-db-thread-flakes-20261107`）。

会话级共享测试库是 StaticPool 单连接（`check_same_thread=False`）：前一个用例的
后台线程还在收尾时，teardown 里 wipe 的 `conn.commit()` 可能踩 CPython 并发症——
`SystemError: <built-in method commit of sqlite3.Connection object at 0x...>
returned NULL without setting an exception`（CI 单次实抓两例、重跑即绿，台账判读
同族：同一 sqlite 连接被多线程同时使用）。台账记录时 wipe 无重试，一次 SystemError
即炸 teardown 整组连坐红——假红窗口，与「测试诚实性」同源（假绿查完，假红也要锁）。

本文件锁四个语义：
1. **commit 相位瞬时 SystemError 必须被整块重试吸收**（DELETE 无条件幂等，
   整块重试安全），重试后数据真删——不是吞异常装绿；
2. **重试耗尽仍失败必须放行原异常**——常驻注入面清不掉必须红，不许静默跳过
   （v0.66 票 01 的病不许回来）；重试次数断言直接引用 `conftest_mod.
   _WIPE_RETRY_ATTEMPTS`，改上限无需同步本文件；
3. **真库冒烟**：真会话 engine 上 wipe 真删常驻注入面、不碰别表
   （AGENTS.md 测试纪律：核心函数不 mock 冒烟）；断言按主键行级事实而非绝对
   计数——会话共享库会累积其他用例的行，绝对计数即顺序敏感（首轮全量实测：
   3325 绿中本臂 1 红，count 期望 1 实得 12，门禁反抓自己，已改）；
4. **execute 相位瞬时 SystemError 同样被整块重试吸收**——台账实抓的是 commit
   相位，execute 相位是同族外推；整块 try 覆盖 connect+DELETE+commit，本臂锁
   外推面不漏。

故障注入说明（如实）：替身只出现在台账已记录的失效边界上（`sqlite3`
factory/cursor_factory 子类），连接、磁盘文件、SQL、DELETE 全部真实执行——
不属于 AGENTS.md 禁止的「让被测函数跳过内部计算逻辑」。真实线程竞争无法在
测试里确定性复现（CI 两例均为单次不可重现），故以失效边界注入锁重试语义。
布防协议：`armed=False` 时全真（建表/插数据用），测试体调 wipe 前置
`armed=True`——注入只命中 wipe 自己的 SQL，不把 create_all 的 DDL commit
打歪（首轮红测踩过）。
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import tests.conftest as conftest_mod

# 台账同款报错原文（CI job 128300693198 实抓），注入臂原样抛出便于对账。
_SYSTEMERROR_COMMIT = (
    "<built-in method commit of sqlite3.Connection object at 0x...> "
    "returned NULL without setting an exception"
)
# execute 相位同族症状（外推，台账实抓的是 commit 相位）。
_SYSTEMERROR_EXECUTE = (
    "<built-in method execute of sqlite3.Cursor object at 0x...> "
    "returned NULL without setting an exception"
)


def _make_flaky_classes(counter: dict):
    """造真实的 sqlite3 连接/游标子类：布防期间按 `counter["poison"]` 相位抛 SystemError。

    `counter` 为共享计数器（闭包注入，跨实例累加）；其余行为全部真实。
    `poison="commit"` 打连接的 commit（台账实抓相位）；`poison="execute"` 打
    dbapi 游标的 execute——SQLAlchemy 经 `cursor.execute` 打 SQL，重写
    `Connection.execute` 拦不到，须经连接子类的 `cursor()` 覆写注入游标工厂
    （`sqlite3.connect` 无 `cursor_factory` 参数，Python 3.11 实测）。
    布防协议见模块 docstring；相位不匹配时守卫直接放行，行为退化为全真。
    """

    class _FlakyCursor(sqlite3.Cursor):
        def execute(self, *args, **kwargs):
            if counter.get("armed") and counter.get("poison") == "execute":
                counter["n"] += 1
                if counter["n"] <= counter["fail_first"]:
                    counter["fails"] = counter.get("fails", 0) + 1
                    raise SystemError(_SYSTEMERROR_EXECUTE)
            return super().execute(*args, **kwargs)

    class _FlakyCommitConnection(sqlite3.Connection):
        def cursor(self, factory=None):
            return super().cursor(_FlakyCursor if factory is None else factory)

        def commit(self):
            if counter.get("armed") and counter.get("poison", "commit") == "commit":
                counter["n"] += 1
                if counter["n"] <= counter["fail_first"]:
                    raise SystemError(_SYSTEMERROR_COMMIT)
            super().commit()

    return _FlakyCommitConnection, _FlakyCursor


def _fresh_engine(tmp_path: Path, conn_cls) -> Engine:
    """临时库文件 + StaticPool 单连接 + 注入连接工厂（与会话库同构：单连接共享）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    db_file = tmp_path / "flaky_wipe.db"
    db_file.touch()
    engine = create_engine(
        f"sqlite:///{db_file}",
        creator=lambda: sqlite3.connect(str(db_file), factory=conn_cls),
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_transient_commit_systemerror_is_retried_and_absorbed(tmp_path, monkeypatch):
    """臂 1：commit 瞬时 SystemError（第 1 次）→ 重试后吸收，且数据真删。"""
    counter = {"n": 0, "armed": False, "fail_first": 1, "poison": "commit"}
    conn_cls, _ = _make_flaky_classes(counter)
    engine = _fresh_engine(tmp_path, conn_cls)
    monkeypatch.setattr(conftest_mod, "_SESSION_DB_ENGINE", engine)

    # 先放一行真数据进常驻注入面，wipe 成功后必须真删（不是吞异常装绿）。
    from lantai.models.tables import CoreMemoryBlock

    with Session(engine) as session:
        session.add(CoreMemoryBlock(id="flaky-1", block="identity", content="x"))
        session.commit()

    counter.update(armed=True, n=0)  # 只对 wipe 的 commit 布防
    try:
        conftest_mod._wipe_resident_channels()  # 红态时此处 SystemError 外抛
        assert counter["n"] == 2  # 失败 1 次 + 重试成功 1 次
        with Session(engine) as session:
            assert session.get(CoreMemoryBlock, "flaky-1") is None
    finally:
        # 显式撤防并还原：红测实测 autouse 夹具的后置 wipe 可能先于 monkeypatch
        # 撤销跑（撕卸顺序跨 pytest 版本不可依赖）。布防引擎留给 teardown 会撞
        # 毒 commit、且真实会话库的常驻注入面在这一例后没人清——两个都不许发生，
        # 故不依赖撕卸顺序，测试体内在 finally 里摘干净。
        counter["armed"] = False
        monkeypatch.undo()


def test_exhausted_retries_reraise_systemerror(tmp_path, monkeypatch):
    """臂 2：commit 持续 SystemError → 重试满上限后放行原异常（宁红不放）。"""
    counter = {"n": 0, "armed": False, "fail_first": 10, "poison": "commit"}
    conn_cls, _ = _make_flaky_classes(counter)
    engine = _fresh_engine(tmp_path, conn_cls)
    monkeypatch.setattr(conftest_mod, "_SESSION_DB_ENGINE", engine)

    counter.update(armed=True, n=0)  # 持续布防，重试救不回
    try:
        with pytest.raises(SystemError, match="returned NULL"):
            conftest_mod._wipe_resident_channels()
        # 直接引用 conftest 常量：耗尽次数 = 重试上限（改上限无需改本臂），
        # 且防止实现提前放弃（比如只试 1 次就放行）——红态时此断言 1 != 3。
        assert counter["n"] == conftest_mod._WIPE_RETRY_ATTEMPTS
    finally:
        counter["armed"] = False
        monkeypatch.undo()  # 同臂 1：把 teardown 从毒引擎上摘下来


def test_wipe_real_engine_clears_resident_and_spares_others():
    """臂 3：真会话 engine 冒烟——真删常驻注入面，别表不动（不 mock 直调）。

    断言按主键行级事实而非绝对计数：会话级共享库会累积其他用例写入的行
    （首轮全量实测 3325 绿里本臂 1 红——MemoryProposal count 期望 1 实得 12，
    顺序敏感断言正是本轮要猎的病，全量门禁当场反抓自己）。
    """
    import lantai.storage.db as db_module
    from lantai.models.tables import CoreMemoryBlock, MemoryProposal

    with Session(db_module.engine) as session:
        session.add(CoreMemoryBlock(id="wipe-smoke-1", block="identity", content="x"))
        session.add(MemoryProposal(id="wipe-smoke-p1", proposal_type="update"))
        session.commit()

    conftest_mod._wipe_resident_channels()

    with Session(db_module.engine) as session:
        assert session.get(CoreMemoryBlock, "wipe-smoke-1") is None  # 常驻行真删
        assert session.get(MemoryProposal, "wipe-smoke-p1") is not None  # 别表行存活


def test_execute_phase_systemerror_is_also_retried(tmp_path, monkeypatch):
    """臂 4：DELETE 相位（dbapi cursor.execute）瞬时 SystemError 同样被整块重试吸收。

    台账实抓的是 commit 相位，execute 相位为同族外推——整块 try 覆盖
    connect+DELETE+commit，本臂锁外推面不漏（双轴自审采纳项）。
    """
    counter = {"n": 0, "armed": False, "fail_first": 1, "poison": "execute", "fails": 0}
    conn_cls, _ = _make_flaky_classes(counter)
    engine = _fresh_engine(tmp_path, conn_cls)
    monkeypatch.setattr(conftest_mod, "_SESSION_DB_ENGINE", engine)

    from lantai.models.tables import CoreMemoryBlock

    with Session(engine) as session:
        session.add(CoreMemoryBlock(id="flaky-exe-1", block="identity", content="x"))
        session.commit()

    counter.update(armed=True, n=0)  # poison/fail_first 已就位
    try:
        conftest_mod._wipe_resident_channels()
        assert counter["fails"] == 1  # 恰好失败一次（第 1 次 DELETE）
        with Session(engine) as session:
            assert session.get(CoreMemoryBlock, "flaky-exe-1") is None  # 重试后真删
    finally:
        counter["armed"] = False
        monkeypatch.undo()
