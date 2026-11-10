"""票 67 红测：会话/事务卫生家族（裸 Session 泄漏 / auto_apply 整体提交 / TOCTOU）。

三面病灶（工单锚点，HEAD=ab91c7cf 亲读行号）：
1. 裸 Session 泄漏：juanzong.py:62 / canshang.py:117 / qijuzhu.py:59 的
   _get_session 裸 return db.get_session()（自建 Session 无 close 无 with），
   方法返回后会话连同活动事务一起弃置（Session↔SessionTransaction 强引用环），
   QueuePool 连接滞留——MCP 卷宗/参商高频调用下池耗尽整库 TimeoutError。
   红臂形态校准（票面 Answer 登记）：CPython 分代 GC 会不定期回收弃置会话，
   「池计数增长」非确定——红载体改为确定性等价面「方法返回后自建会话仍处于
   活动事务（未关闭）」；池耗尽形态保留为绿锁集成臂（小池 50 连发不炸 + 归零）。
2. sanxing.py:413-414 auto_apply 对传入 session 直接 s.commit()——调用方
   中间态被整体提交/回滚。红臂：外层会话持未提交探针行，自愈内部真失败
   （SQLite RAISE(ABORT) 触发器——真库约束拒绝，非 mock）→ 修复前探针行被
   整体回滚=红；修复后自愈写走独立短会话，外层事务原样=绿。
3. sanxing.py:331-400 采纳流程扫描时点快照与落库之间无重验（TOCTOU）——
   已撤回记忆可被物化。红臂经 evaluate_health 真公共接缝编排并发落点（重检
   后、落库前第二会话真写撤回——时序替身注入在接缝，被测链路全真，票 52
   handler 接缝注入同口径）：修复前撤回被覆写=红；修复后写入前重验、谓词
   不成立跳过该候选不报错（宁 miss 不脏写）=绿，正常候选照常落库。

纪律：真引擎直调禁 mock 被测链路；测试库一律 tmp_path/内存独立库
（红线：一个连接都不许碰 .env LANTAI_HOME 真实开发库）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.canshang import CanshangEngine
from lantai.cognition.qijuzhu import QijuzhuEngine
from lantai.cognition.sanxing import SanxingEngine
from lantai.cognition.juanzong import JuanzongEngine
from lantai.models.tables import MemoryItem
from lantai.services.dossier_service import create_dossier_service, get_dossier_service

import lantai.storage.db as db_module

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _expired(**kw) -> MemoryItem:
    """票 67 标准候选种子：valid_to 已过、非程序铁律（三省 expired 病灶判定）。"""
    return MemoryItem(
        content=kw.pop("content", "过期凭证"),
        status="active",
        valid_to=NOW - timedelta(days=1),
        decay_class="linear",
        **kw,
    )


def _file_engine(tmp_path, *, pool_size=None, max_overflow=None, pool_timeout=None):
    kw = {"connect_args": {"check_same_thread": False}}
    if pool_size is not None:
        kw.update(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout)
    eng = create_engine(f"sqlite:///{tmp_path / 'ticket67.db'}", **kw)
    SQLModel.metadata.create_all(eng)
    return eng


def _install_recording_factory(monkeypatch, eng):
    """接缝替身（票 52 handler 注入同口径）：db.get_session 指向独立测试库并
    逐一记录自建会话——被测引擎的会话获取与全部业务逻辑照真执行。"""
    recorded: list[Session] = []

    def factory() -> Session:
        s = Session(eng)
        recorded.append(s)
        return s

    monkeypatch.setattr(db_module, "get_session", factory)
    return recorded


# ── 臂 A1：裸 Session 泄漏（红载体：方法返回后自建会话仍在活动事务）────────


def test_arm_a1a_juanzong_owned_session_closed_after_return(tmp_path, monkeypatch):
    recorded = _install_recording_factory(monkeypatch, _file_engine(tmp_path))
    JuanzongEngine().create_dossier("泄漏臂卷宗")
    assert recorded, "自建会话路径未被触发（接缝替身失效）"
    s = recorded[0]
    assert not s.in_transaction(), (
        "create_dossier 返回后自建 Session 仍处于活动事务（未 close，裸 Session 泄漏）"
    )


def test_arm_a1b_canshang_owned_session_closed_after_return(tmp_path, monkeypatch):
    recorded = _install_recording_factory(monkeypatch, _file_engine(tmp_path))
    CanshangEngine().audit_consistency()
    assert recorded, "自建会话路径未被触发（接缝替身失效）"
    s = recorded[0]
    assert not s.in_transaction(), (
        "audit_consistency 返回后自建 Session 仍处于活动事务（未 close，裸 Session 泄漏）"
    )


def test_arm_a1c_qijuzhu_owned_session_closed_after_return(tmp_path, monkeypatch):
    recorded = _install_recording_factory(monkeypatch, _file_engine(tmp_path))
    QijuzhuEngine().get_daily_chronicle()
    assert recorded, "自建会话路径未被触发（接缝替身失效）"
    s = recorded[0]
    assert not s.in_transaction(), (
        "get_daily_chronicle 返回后自建 Session 仍处于活动事务（未 close，裸 Session 泄漏）"
    )


# ── 臂 A2（绿锁集成）：小池 50 连发不炸 + 活跃连接归零 ─────────────────────


def test_arm_a2_pool_survives_50_unclosed_calls(tmp_path, monkeypatch):
    eng = _file_engine(tmp_path, pool_size=2, max_overflow=2, pool_timeout=0.5)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(eng))
    failures: list[str] = []
    for i in range(50):
        try:
            JuanzongEngine().create_dossier(f"连发卷宗{i}")
            CanshangEngine().audit_consistency()
            QijuzhuEngine().get_daily_chronicle()
        except Exception as e:  # noqa: BLE001 - 泄漏故障形态收集器
            failures.append(f"iter{i}: {type(e).__name__}: {e}")
            break
    assert not failures, f"池耗尽（裸 Session 泄漏故障形态）：{failures[:3]}"
    assert eng.pool.checkedout() == 0, f"活跃连接未归零：{eng.pool.checkedout()}"


# ── 臂 B：auto_apply 整体提交调用方会话（保存点/独立会话语义）──────────────


def test_arm_b_caller_midstate_survives_internal_failure(tmp_path):
    eng = _file_engine(tmp_path)
    # 真库约束拒绝（非 mock）：RAISE(ABORT) 触发器封死 status='archived' 的 UPDATE
    with eng.connect() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER t67_archive_guard BEFORE UPDATE ON memoryitem "
                "WHEN NEW.status='archived' "
                "BEGIN SELECT RAISE(ABORT, 'ticket67-guard: archive forbidden'); END;"
            )
        )
        conn.commit()

    seed = Session(eng)
    seed.add(_expired(id="mem_guard"))
    seed.commit()
    seed.close()

    # 外层调用方会话：先写一行探针（不 commit）——自愈不得动它
    outer = Session(eng)
    outer.add(MemoryItem(id="mem_probe_outer", content="外层未提交中间态", status="active"))
    outer.flush()

    engine = SanxingEngine(session=outer)
    with pytest.raises(Exception) as ei:
        engine.generate_remedies(auto_apply=True, now=NOW)
    assert "ticket67-guard" in str(ei.value), f"自愈内部失败未真发生：{ei.value}"

    # 外层事务原样：探针行仍在会话事务中、未提交、未被回滚
    assert outer.in_transaction(), "自愈内部失败后调用方事务被连坐关闭"
    probe = outer.get(MemoryItem, "mem_probe_outer")
    assert probe is not None and probe.status == "active", "外层探针行被整体回滚丢失"

    # 且探针/半途自愈均未落库（其他连接不可见——未被整体提交）
    outsider = Session(eng)
    assert outsider.get(MemoryItem, "mem_probe_outer") is None, "外层中间态被整体提交"
    assert outsider.get(MemoryItem, "mem_guard").status == "active", "自愈半途行被提交"
    outsider.close()


# ── 臂 C：TOCTOU——扫描后落库前撤回，修复前物化成功（撤回被覆写）────────────


class _RetractAfterScanEngine(SanxingEngine):
    """时序编排替身：evaluate_health 全链照真执行，仅在「扫描返回后、落库前」
    的真实并发窗口内以第二会话执行真撤回（第二会话真 UPDATE + 真 commit）。"""

    retract_ids: tuple[str, ...] = ()

    def evaluate_health(self, now=None):
        snap = super().evaluate_health(now=now)
        if self.retract_ids:
            s2 = Session(self.session.get_bind())
            try:
                for mid in self.retract_ids:
                    row = s2.get(MemoryItem, mid)
                    row.status = "retracted"
                    s2.add(row)
                s2.commit()
            finally:
                s2.close()
        return snap


def test_arm_c_toctou_retracted_candidate_skipped_not_materialized(tmp_path):
    eng = _file_engine(tmp_path)
    seed = Session(eng)
    seed.add(_expired(id="mem_ok"))
    seed.add(_expired(id="mem_bye"))
    seed.commit()
    seed.close()

    s0 = Session(eng)
    engine = _RetractAfterScanEngine(session=s0)
    engine.retract_ids = ("mem_bye",)
    res = engine.generate_remedies(auto_apply=True, now=NOW)

    executed = {r.target_id: r.executed for r in res.remedies if r.defect_type == "expired"}
    assert executed.get("mem_ok") is True, "正常候选被 TOCTOU 修复误伤（该落未落）"
    assert executed.get("mem_bye") is False, "已撤回候选仍被执行（宁 miss 不脏写破约）"
    assert res.applied_count == 1, f"applied_count 与执行面不符：{res.applied_count}"

    verify = Session(eng)
    assert verify.get(MemoryItem, "mem_bye").status == "retracted", (
        "扫描后落库前的撤回被 auto_apply 覆写物化（TOCTOU 病灶：已撤回记忆被物化）"
    )
    assert verify.get(MemoryItem, "mem_ok").status == "archived", "正常候选未落库"
    verify.close()


# ── 臂 D：回归锁（自有会话路径持久化 + dossier_service 注入会话）────────────


def test_arm_d1_owned_session_path_still_persists(tmp_path, monkeypatch):
    eng = _file_engine(tmp_path)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(eng))
    seed = Session(eng)
    seed.add(_expired(id="mem_owned"))
    seed.commit()
    seed.close()

    res = SanxingEngine().generate_remedies(auto_apply=True, now=NOW)  # 自建会话路径
    assert res.applied_count == 1

    verify = Session(eng)
    assert verify.get(MemoryItem, "mem_owned").status == "archived", (
        "自建会话路径的自愈持久化被独立会话改造误伤"
    )
    verify.close()


def test_arm_d2_dossier_service_accepts_injected_session(tmp_path, monkeypatch):
    recorded = _install_recording_factory(monkeypatch, _file_engine(tmp_path))
    # 显式独立会话（worker 长连接复用语义——不经过 db.get_session 接缝）
    s = Session(_file_engine(tmp_path))
    d = create_dossier_service("注入会话卷宗", goal="票 67", session=s)
    got = get_dossier_service(d["id"], session=s)
    assert got is not None and got["title"] == "注入会话卷宗"
    # 注入会话路径不得自建新会话（worker 长连接复用语义保持）
    assert recorded == [], "service 注入 session 后仍自建会话（长连接复用语义破坏）"
