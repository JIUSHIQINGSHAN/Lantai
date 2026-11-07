"""票 23：计数器/整行快照 RMW 丢更新——原子 UPDATE / 条件提交与并发回归锁。

病灶（`.scratch/v0.68-next-window-2026-11/issues/23-counter-snapshot-rmw-lost-updates.md`）：
计数器与判定类写路径全部是「SELECT 快照 → Python 内算术/判定 → 无条件 ORM
写回」（pysqlite 惰性 BEGIN 下纯 SELECT 不进写事务，读与写之间零隔离）：
反馈/保活/延期计数可被并发方整批吃掉（F1/F2/F3/F4）、考功与守门降权可按
陈旧快照覆写并发反馈（F6/F7）、遗忘扫描可归档扫描窗口内刚被保活的记忆
（W1）、纠错可丢并发 correction 历史且 version 重号（S8）、核心记忆块可双
插双行 / version 增量丢失（F5）、知命转移与回滚可按陈旧计划覆写已推进的
生命周期（S7/rollback）、终路合并可丢并发纠错（terminal merge）。

修复范式（票面三类机械修 + 票 22 cas_update 收口）：①纯计数器 → 单语句原子
UPDATE（SQL 表达式对最新已提交值求值，语义 = 串行执行逐字一致）；②判定类 →
条件 UPDATE 写时重验判定输入（rowcount=0 → 跳过/冲突，宁 miss 不脏写）；
③JSON 追加/整行覆写 → 版本前置 CAS（认领式 `WHERE version=:扫描值`，败方
重读重试或如实拒绝）。全部走惰性 BEGIN「写阶段第一条 DML」位。

测试形态（票面 TDD 要求「真内存库 + 真线程 Barrier 或跨 session 交错注入」，
同票 22 拍板）：选**跨 session 交错注入**。生产竞态窗口全部开在函数体内部
（读与写提交之间），Barrier 无法钉进那里；注入缝选真实外部边界（时钟
utcnow、ACL ensure_can_delete/_check_access/_ensure_can_adjudicate、注入闸
assert_no_prompt_injection、id 生成 new_id、遗忘豁免判定 _ttl_exempt、
守门规则 check_rules），垫片**先调真实现、
再在返回前执行 B 侧完整真实流程**（第二 session 全真状态变更）——被测链路
零 mock，只把交错点钉死。全程单线程顺序执行：无 sleep、无 jitter、无超时
依赖，逐例确定性。

替身面声明：embed/chat_json 是外部 LLM 边界（conftest autouse 替身，纪律
允许）；其余垫片全部是「先调真、再注交错」的透传包装，不替任何被测逻辑；
DB、FTS、状态机、判定规则全链全真。所有垫片都在测试体内 mock.patch（with
退出即还原，先于 fixture 撕卸——param_env 两层打点，票 21 教训同款规避）。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import select

from lantai.cognition.kaogong import KaogongEngine
from lantai.cognition.shishi import ShishiEngine
from lantai.cognition.zhiming import LifecyclePhase, ZhimingEngine, ZhimingTransition
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.evolution.promoter import _make_checkpoint, rollback
from lantai.evolution.reflector import record_feedback
from lantai.gate.decision import decide
from lantai.memory.forgetting import apply_forgetting
from lantai.models.enums import GateDecision
from lantai.models.tables import (
    ConflictEvent,
    CoreMemoryBlock,
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryItem,
    RetrievalEvent,
)
from lantai.observability.retrieval_log import backfill_used_ids, mark_vetoed
from lantai.services.candidate_service import (
    CandidateStateConflict,
    defer_candidate,
    undo_candidate_defer,
)
from lantai.services.memory_service import _dedup_merge, put_core_memory
from lantai.services.record_ops_service import correct_memory

_P = Principal(user_id="u_a", tenant_id=None, allowed_lanes=None)


def _mem(mid: str, content: str = "大哥偏好浙江新昌大佛龙井茶", **over) -> MemoryItem:
    fields = dict(
        id=mid,
        key=f"key_{mid}",
        content=content,
        memory_type="semantic",
        lane="general",
        status="active",
        user_id="u_a",
        decay_score=0.9,
        source_ids=[],
        confidence=0.8,
        importance=0.5,
    )
    fields.update(over)
    return MemoryItem(**fields)


def _cand(cid: str, summary: str, **over) -> MemoryCandidate:
    fields = dict(
        id=cid,
        document_id=f"doc_{cid}",
        summary=summary,
        claims=[summary[:20]],
        actions=[],
        lane="general",
        status="pending_review",
        user_id="u_a",
        extractor_confidence=0.9,
    )
    fields.update(over)
    return MemoryCandidate(**fields)


class TestF1FeedbackCounterLoss:
    """F1（reflector.py:47-50）：并发反馈的 use_count/helpful_count 被快照覆写吃掉。

    注入缝：record_feedback 的 utcnow（:50，在计数自增 :47-49 之后、提交
    :101 之前）。B 侧完整真实 record_feedback 在缝内提交；A 继续用旧快照
    值写回——两条相同反馈按串行语义 use_count 应为 2，现状丢成 1。
    """

    def test_two_concurrent_feedbacks_both_counted(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_f1"))
            s.commit()

        import lantai.evolution.reflector as refl_mod

        racing = {"done": False}
        real_utcnow = refl_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                record_feedback(
                    "mem_f1", "q", helped=True, user_accepted=True, hallucination_risk=0.0
                )
            return out

        with patch("lantai.evolution.reflector.utcnow", side_effect=_utcnow_with_interleave):
            record_feedback("mem_f1", "q", helped=True, user_accepted=True, hallucination_risk=0.0)

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f1")
            assert m.use_count == 2, (
                f"并发反馈丢计数：use_count={m.use_count}（快照 RMW 覆写，票面 F1 现状）"
            )
            assert m.helpful_count == 2, f"helpful_count={m.helpful_count} 同源丢失"
            assert m.importance == pytest.approx(0.9), (
                f"importance={m.importance} 应为两次 +0.2（0.5+0.2+0.2）"
            )


class TestF2KeepaliveInjectionCount:
    """F2（retrieval_log.py:194-195 / :250-257）：注入计数丢更新 / 重放双计 / vetoed 复活。"""

    def test_two_events_same_memory_both_counted(self, param_env):
        """F2a：两个不同事件各自回执同一条记忆——injection_count 应为 2。

        注入缝：_touch_keepalive 的 utcnow（:195，在 item 读 :176 之后、
        提交 :198 之前）——垫片在第 2 次调用（保活段）注交错，第 1 次
        （回执落库段）放行。
        """
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_f2a"))
            s.add(
                RetrievalEvent(
                    id="ev_1",
                    trace_id="t1",
                    query_text="q",
                    query_norm_hash="h1",
                    param_snapshot_hash="test",
                    result_ids=["mem_f2a"],
                    used_ids=[],
                    receipt_status="pending",
                    latency_ms=1,
                )
            )
            s.add(
                RetrievalEvent(
                    id="ev_2",
                    trace_id="t2",
                    query_text="q",
                    query_norm_hash="h2",
                    param_snapshot_hash="test",
                    result_ids=["mem_f2a"],
                    used_ids=[],
                    receipt_status="pending",
                    latency_ms=1,
                )
            )
            s.commit()

        import lantai.observability.retrieval_log as rl_mod

        state = {"calls": 0, "busy": False}
        real_utcnow = rl_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            state["calls"] += 1
            if state["calls"] >= 2 and not state["busy"]:
                state["busy"] = True
                try:
                    backfill_used_ids("ev_2", ["mem_f2a"])
                finally:
                    state["busy"] = False
            return out

        with patch(
            "lantai.observability.retrieval_log.utcnow", side_effect=_utcnow_with_interleave
        ):
            backfill_used_ids("ev_1", ["mem_f2a"])

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f2a")
            assert m.injection_count == 2, (
                f"两事件各注入一次：injection_count={m.injection_count}（保活读-写窗口丢更新）"
            )
            assert s.get(RetrievalEvent, "ev_1").receipt_status == "acked"

    def test_duplicate_replay_idempotent(self, param_env):
        """F2b：同一事件并发重放同一回执——保活只认「首次回执」，计数应为 1。

        注入缝：backfill_used_ids 的 utcnow（:254，覆盖写之前）——B 侧重放
        在缝内完整提交。现状两路都按扫描期 used_ids=[] 算增量，双双保活。
        """
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_f2b"))
            s.add(
                RetrievalEvent(
                    id="ev_r",
                    trace_id="t",
                    query_text="q",
                    query_norm_hash="hr",
                    param_snapshot_hash="test",
                    result_ids=["mem_f2b"],
                    used_ids=[],
                    receipt_status="pending",
                    latency_ms=1,
                )
            )
            s.commit()

        import lantai.observability.retrieval_log as rl_mod

        racing = {"done": False}
        real_utcnow = rl_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                backfill_used_ids("ev_r", ["mem_f2b"])
            return out

        with patch(
            "lantai.observability.retrieval_log.utcnow", side_effect=_utcnow_with_interleave
        ):
            backfill_used_ids("ev_r", ["mem_f2b"])

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f2b")
            assert m.injection_count == 1, (
                f"同一回执重放不得重复保活：injection_count={m.injection_count}（票面 F2 重放双计）"
            )

    def test_backfill_cannot_revive_vetoed_event(self, param_env):
        """F2c：回执与 veto 竞态——vetoed 是终态（ADR-0084），不得被迟到处回执拉回 acked。

        注入缝同 F2b（:254）。现状覆盖写无前置态守护，veto 被整体覆回 acked
        并保活——假注入计数从后门回来。
        """
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_f2c"))
            s.add(
                RetrievalEvent(
                    id="ev_v",
                    trace_id="t",
                    query_text="q",
                    query_norm_hash="hv",
                    param_snapshot_hash="test",
                    result_ids=["mem_f2c"],
                    used_ids=[],
                    receipt_status="pending",
                    latency_ms=1,
                )
            )
            s.commit()

        import lantai.observability.retrieval_log as rl_mod

        racing = {"done": False}
        real_utcnow = rl_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                assert mark_vetoed("ev_v") is True
            return out

        with patch(
            "lantai.observability.retrieval_log.utcnow", side_effect=_utcnow_with_interleave
        ):
            backfill_used_ids("ev_v", ["mem_f2c"])

        with session_factory() as s:
            ev = s.get(RetrievalEvent, "ev_v")
            assert ev.receipt_status == "vetoed", (
                f"vetoed 被迟到处回执复活为 {ev.receipt_status!r}（ADR-0084 终态被覆写）"
            )
            assert s.get(MemoryItem, "mem_f2c").injection_count == 0, "被否决一轮不得回写保活计数"


class TestF3DedupMergeImportanceLoss:
    """F3（memory_service.py:120-121）：合并保活 +0.1 被并发反馈的快照覆写吃掉。

    注入缝：_dedup_merge 的 utcnow（:120）。B 侧完整真实 record_feedback
    （+0.2）在缝内提交；A 继续按旧快照 0.5+0.1 写回——串行语义应两次都落
    （0.5+0.1+0.2=0.8），现状丢成 0.6。
    """

    def test_merge_bump_and_feedback_both_apply(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_f3"))
            s.commit()

        import lantai.services.memory_service as ms_mod

        racing = {"done": False}
        real_utcnow = ms_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                record_feedback(
                    "mem_f3", "q", helped=True, user_accepted=True, hallucination_risk=0.0
                )
            return out

        with session_factory() as s:
            target = s.get(MemoryItem, "mem_f3")
            with patch(
                "lantai.services.memory_service.utcnow", side_effect=_utcnow_with_interleave
            ):
                _dedup_merge(s, target, 0.9, _P)

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f3")
            assert m.importance == pytest.approx(0.8), (
                f"importance={m.importance}：合并 +0.1 与反馈 +0.2 应都落库（串行语义 0.8）"
            )
            assert m.last_used_at is not None


class TestF4DeferCount:
    """F4（candidate_service.py:211/:253）：延期计数与撤销按快照 RMW 丢更新/互覆。"""

    def test_concurrent_defers_honest_or_conflict(self, param_env):
        """F4a：并发双 defer——现状两路都报成功且 defer_count 丢成 1、due 被后写覆盖。

        修复语义：写时重验扫描期 due（乐观前置）→ A 如实 CandidateStateConflict，
        B 的延期完整保留（票 22 已建立的冲突契约 :197/:202 同形）。
        """
        session_factory, _ = param_env
        due0 = utcnow()
        with session_factory() as s:
            s.add(_cand("cand_f4", "延期并发核验", review_due_at=due0))
            s.commit()

        import lantai.services.candidate_service as cs_mod

        b_result: dict = {}
        racing = {"done": False}
        real_check = cs_mod._ensure_can_adjudicate

        def _check_with_interleave(principal, c):
            out = real_check(principal, c)
            if not racing["done"]:
                racing["done"] = True
                b_result.update(defer_candidate("cand_f4", 3, principal=_P))
            return out

        with patch(
            "lantai.services.candidate_service._ensure_can_adjudicate",
            side_effect=_check_with_interleave,
        ):
            with pytest.raises(CandidateStateConflict):
                defer_candidate("cand_f4", 7, principal=_P)

        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_f4")
            assert c.defer_count == 1, f"defer_count={c.defer_count}：A 的延期已如实拒绝，不应计 2"
            assert c.review_due_at is not None
            # SQLite DateTime 回读 naive（UTC 墙钟），b_result 是 aware——按时刻比较
            b_due = datetime.fromisoformat(b_result["review_due_at"])
            assert c.review_due_at == b_due.replace(tzinfo=None), (
                "A 的陈旧延期写回不得覆盖 B 已提交的 due"
            )

    def test_undo_must_not_erase_concurrent_defer(self, param_env):
        """F4b：撤销延期 × 并发新延期——现状 undo 按扫描期旧值把 B 的延期整体抹掉。

        修复语义：同 F4a——A 的 undo 与 B 的 due 冲突 → 如实拒绝，B 的延期
        （count=2、previous=due1）完整保留。
        """
        session_factory, _ = param_env
        due0 = utcnow() - timedelta(days=7)
        due1 = utcnow()
        with session_factory() as s:
            s.add(
                _cand(
                    "cand_f4b",
                    "撤销并发核验",
                    review_due_at=due1,
                    previous_review_due_at=due0,
                    deferred_at=due1,
                    defer_count=1,
                )
            )
            s.commit()

        import lantai.services.candidate_service as cs_mod

        racing = {"done": False}
        real_check = cs_mod._ensure_can_adjudicate

        def _check_with_interleave(principal, c):
            out = real_check(principal, c)
            if not racing["done"]:
                racing["done"] = True
                defer_candidate("cand_f4b", 3, principal=_P)
            return out

        with patch(
            "lantai.services.candidate_service._ensure_can_adjudicate",
            side_effect=_check_with_interleave,
        ):
            with pytest.raises(CandidateStateConflict):
                undo_candidate_defer("cand_f4b", principal=_P)

        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_f4b")
            assert c.defer_count == 2, f"defer_count={c.defer_count}：B 的延期被 undo 抹掉"
            assert c.previous_review_due_at is not None, "previous 被 undo 清空——B 的延期审计丢失"


class TestF5CoreMemoryBlock:
    """F5（memory_service.py:598-617 / shishi.py:145-167 / tables.py 无唯一约束）：
    并发首写双行、并发更新 version 增量丢失。"""

    def test_concurrent_first_writes_single_row(self, param_env):
        """F5a：并发首写同一 (block, namespace, user)——现状双插两条平行行。

        注入缝：put_core_memory 的 new_id（:608，INSERT 分支）。修复语义：
        (block, namespace, user_id) 唯一约束兜底，败方 IntegrityError → 重读
        走更新路（末写者胜），恰好一行。
        """
        session_factory, _ = param_env

        import lantai.services.memory_service as ms_mod

        racing = {"done": False}
        real_new_id = ms_mod.new_id

        def _new_id_with_interleave(prefix):
            out = real_new_id(prefix)
            if not racing["done"]:
                racing["done"] = True
                put_core_memory("policy", "B方写入的核心记忆")
            return out

        with patch("lantai.services.memory_service.new_id", side_effect=_new_id_with_interleave):
            put_core_memory("policy", "A方写入的核心记忆")

        with session_factory() as s:
            rows = s.exec(
                select(CoreMemoryBlock).where(
                    CoreMemoryBlock.block == "policy", CoreMemoryBlock.namespace == "default"
                )
            ).all()
            assert len(rows) == 1, f"并发首写双行：{[r.id for r in rows]}（票面 F5 现状）"
            assert rows[0].content == "A方写入的核心记忆"
            assert rows[0].version == 2

    def test_concurrent_updates_version_increments(self, param_env):
        """F5b：并发更新同块——现状两路都按扫描期 version=1 算 +1，编年史重号 2。

        注入缝：seal_core_memory 的 utcnow（shishi.py:162，更新分支）。
        修复语义：version 库端自增（version=version+1），两路各 +1 → 3。
        """
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(
                CoreMemoryBlock(
                    id="cmb_f5b",
                    block="task",
                    namespace="default",
                    content="v1",
                    user_id="default",
                    version=1,
                )
            )
            s.commit()

        import lantai.cognition.shishi as sh_mod

        racing = {"done": False}
        real_utcnow = sh_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                put_core_memory("task", "put方并发更新")
            return out

        with patch("lantai.cognition.shishi.utcnow", side_effect=_utcnow_with_interleave):
            ShishiEngine(principal=None).seal_core_memory("task", "封存方并发更新")

        with session_factory() as s:
            row = s.get(CoreMemoryBlock, "cmb_f5b")
            assert row.version == 3, (
                f"version={row.version}：两次并发更新应各 +1（1→2→3），快照 RMW 重号"
            )
            assert row.content == "封存方并发更新"


class TestF6KaogongStaleDemote:
    """F6（kaogong.py:301-313）：考功降权按扫描期快照绝对值覆写并发反馈。

    注入缝：_check_access（:299，逐行评估之前）。B 侧完整真实反馈
    （+0.2 → 0.7）在缝内提交；A 继续按旧快照 0.5 判降权写 0.1——把刚被
    表扬的记忆打成低价值。修复语义：写时重验扫描期 importance/tier，
    冲突则跳过（宁 miss 不脏写）。
    """

    def test_feedback_during_scan_not_overwritten(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(
                _mem(
                    "mem_f6",
                    use_count=5,
                    helpful_count=0,
                    importance=0.5,
                    tier="working",
                )
            )
            s.commit()

        racing = {"done": False}
        real_check = KaogongEngine._check_access

        def _check_with_interleave(self, *, owner_id, tenant_id):
            out = real_check(self, owner_id=owner_id, tenant_id=tenant_id)
            if not racing["done"]:
                racing["done"] = True
                record_feedback(
                    "mem_f6", "q", helped=True, user_accepted=True, hallucination_risk=0.0
                )
            return out

        with patch.object(KaogongEngine, "_check_access", _check_with_interleave):
            report = KaogongEngine(principal=_P).run_cycle()

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f6")
            assert m.importance == pytest.approx(0.7), (
                f"importance={m.importance}：并发反馈 +0.2（0.5→0.7）被考功降权快照覆写（票面 F6）"
            )
            assert m.helpful_count == 1
            assert report["demoted"] == 0, "降权与扫描期状态冲突时应如实跳过而非计入 demoted"


class TestF7SalienceDemoteStale:
    """F7（decision.py:132-149）：守门 salience 降权按扫描期快照覆写并发反馈。

    注入缝：check_rules（:130，规则命中之后、降权写 :135 之前）。互斥规则
    「启用/禁用」确定性命中；B 侧完整真实反馈（+0.2 → 0.55）在缝内提交；
    A 继续按旧快照 0.35-0.2=0.15 写回，还落 checkpoint+账本——账实分离。
    修复语义：条件 UPDATE 重验 importance，冲突则不降权、不落账。
    """

    def test_bump_during_gate_not_demoted(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_cand("cand_f7", "自动同步已禁用"))
            s.add(_mem("mem_f7", content="自动同步已启用", importance=0.35))
            s.commit()

        import lantai.gate.decision as gd_mod

        racing = {"done": False}
        real_check_rules = gd_mod.check_rules

        def _rules_with_interleave(new_text, existing_text):
            out = real_check_rules(new_text, existing_text)
            if out and not racing["done"]:
                racing["done"] = True
                record_feedback(
                    "mem_f7", "q", helped=True, user_accepted=True, hallucination_risk=0.0
                )
            return out

        with patch("lantai.gate.decision.check_rules", side_effect=_rules_with_interleave):
            out = decide("cand_f7")

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_f7")
            assert m.importance == pytest.approx(0.55), (
                f"importance={m.importance}：并发反馈 +0.2（0.35→0.55）被降权快照覆写成 0.15（票面 F7）"
            )
            ckpts = s.exec(
                select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == "mem_f7")
            ).all()
            evs = s.exec(
                select(ConflictEvent).where(
                    ConflictEvent.memory_id == "mem_f7", ConflictEvent.kind == "salience_demote"
                )
            ).all()
            assert not ckpts and not evs, (
                f"降权未发生却落了 checkpoint {len(ckpts)} 行 / salience_demote 账本 {len(evs)} 行（账实分离）"
            )
            assert out["decision"] != GateDecision.REJECT, "降权跳过后仍应放行（弱记忆不挡新信息）"


class TestW1ArchiveVsFeedback:
    """W1（forgetting.py:163-165）：遗忘扫描按旧快照归档扫描窗口内刚被保活的记忆。

    注入缝：_ttl_exempt（:153，归档判定之前）。B 侧完整真实反馈
    （helpful_count 0→1、last_used_at 落定）在缝内提交；A 按扫描期快照
    （零帮助、70 天未用）判 TTL 到期写 archived——archived 没有 undo 入口。
    修复语义：归档条件 UPDATE 写时重验判定输入（status/tier/importance/
    helpful_count/last_used_at），有变化即跳过，下轮重判。
    """

    def test_feedback_during_scan_not_archived(self, param_env):
        session_factory, _ = param_env
        old = utcnow() - timedelta(days=70)
        with session_factory() as s:
            s.add(
                _mem(
                    "mem_w1",
                    tier="working",
                    created_at=old,
                    last_used_at=None,
                    helpful_count=0,
                    use_count=0,
                    injection_count=0,
                    importance=0.3,
                    decay_class="episodic",
                )
            )
            s.commit()

        import lantai.memory.forgetting as fg_mod

        racing = {"done": False}
        real_exempt = fg_mod._ttl_exempt

        def _exempt_with_interleave(m):
            out = real_exempt(m)
            if not racing["done"]:
                racing["done"] = True
                record_feedback(
                    "mem_w1", "q", helped=True, user_accepted=True, hallucination_risk=0.0
                )
            return out

        with patch("lantai.memory.forgetting._ttl_exempt", side_effect=_exempt_with_interleave):
            apply_forgetting()

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_w1")
            assert m.status == "active", (
                f"status={m.status!r}：扫描窗口内的保活反馈被旧快照归档（archived 无 undo，票面 W1）"
            )
            assert m.helpful_count == 1


class TestS8CorrectionHistoryLoss:
    """S8（record_ops_service.py:405-470）：并发纠错丢 correction 历史 + version 重号。

    注入缝：correct_memory._run 的 utcnow（:421，corrections "at" 时间戳，
    在守卫之后、版本 CAS claim :453 之前——StaticPool 单连接下缝必须在 A
    首条 DML 之前，B 才能独立提交）。B 侧完整真实纠错（v2，
    corrections=[B]）在缝内提交；A 继续按扫描期 version=1 整列写回——
    B 的 correction 静默消失，version 双双写 2。修复语义：版本前置 CAS
    （WHERE version=扫描值），败方回滚重读重拼（有界重试、无 jitter）——
    corrections 两条都在，version=3。
    """

    def test_two_concurrent_corrections_both_recorded(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_s8", content="纠错前原文"))
            s.commit()

        import lantai.services.record_ops_service as ro_mod

        racing = {"done": False}
        real_utcnow = ro_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory("mem_s8", new_content="B方更正后的正文", reason="票23并发")
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        out_a = None
        with patch(
            "lantai.services.record_ops_service.utcnow", side_effect=_utcnow_with_interleave
        ):
            out_a = correct_memory("mem_s8", new_content="A方更正后的正文", reason="票23并发")

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_s8")
            prov = m.provenance or {}
            corrections = prov.get("corrections") or []
            assert len(corrections) == 2, (
                f"corrections={len(corrections)} 条：并发纠错的 correction 历史被整列写回吃掉（票面 S8）"
            )
            assert [c["old_content"] for c in corrections] == ["纠错前原文", "B方更正后的正文"]
            assert m.version == 3, f"version={m.version}：两次纠错应各 +1（1→2→3）"
            assert m.content == "A方更正后的正文"
            assert out_a.get("ok") and out_a.get("version") == 3


class TestS7LifecycleTransitionStale:
    """S7（zhiming.py:306-317）：知命转移按陈旧计划覆写已推进的生命周期。

    注入缝：_check_access（:301，转移落地之前）。B 侧完整真实 retire
    （active→retired，status 同步 archived）在缝内提交；A 继续把 retired
    覆写回 weakened——retired_at 还在、status 还是 archived 的矛盾中间态。
    修复语义：CAS `WHERE lifecycle_status=from_phase`，败方如实拒绝。
    """

    def test_concurrent_transitions_no_overwrite(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_s7", user_id=None))
            s.commit()

        racing = {"done": False}
        real_check = ZhimingEngine._check_access

        def _check_with_interleave(self, *, owner_id, tenant_id):
            out = real_check(self, owner_id=owner_id, tenant_id=tenant_id)
            if not racing["done"]:
                racing["done"] = True
                ZhimingEngine(principal=None).apply_transition(
                    ZhimingTransition(
                        memory_id="mem_s7",
                        from_phase=LifecyclePhase.ACTIVE,
                        to_phase=LifecyclePhase.RETIRED,
                        effective_confidence=0.5,
                        decay_factor=0.5,
                        reason="票23并发退役",
                    )
                )
            return out

        def _weaken():
            ZhimingEngine(principal=None).apply_transition(
                ZhimingTransition(
                    memory_id="mem_s7",
                    from_phase=LifecyclePhase.ACTIVE,
                    to_phase=LifecyclePhase.WEAKENED,
                    effective_confidence=0.5,
                    decay_factor=0.5,
                    reason="票23陈旧计划",
                )
            )

        with patch.object(ZhimingEngine, "_check_access", _check_with_interleave):
            with pytest.raises(Exception) as exc_info:
                _weaken()
            assert not isinstance(exc_info.value, PermissionError), "败方应报状态冲突而非越权"

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_s7")
            assert m.lifecycle_status == "retired", (
                f"lifecycle_status={m.lifecycle_status!r}：陈旧转移计划覆写了已落地的 retire（票面 S7）"
            )
            assert m.status == "archived", "retire 分支的 status 归档不得被削弱分支回退"


class TestRollbackStaleOverwrite:
    """rollback（promoter.py:757-799）：回滚整行 setattr 按陈旧快照覆写并发纠错。

    注入缝：ensure_can_delete（:765，行读取 :758 之后、整行写回 :796-799
    之前）。B 侧完整真实纠错（v2→v3）在缝内提交；A 继续把 v1 快照整行
    写回且 version=1+1=2——B 的纠错消失、版本倒退重号。修复语义：入口
    version CAS，行已变化则拒绝回滚（宁 miss 不脏写）。
    """

    def test_rollback_refused_when_memory_changed(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            m = _mem("mem_rb", content="v1正文")
            m.version = 1
            s.add(m)
            _make_checkpoint(s, m, {}, "", trigger="seed_v1")
            s.commit()
            m.content = "v2正文"
            m.version = 2
            _make_checkpoint(s, m, {}, "", trigger="seed_v2")
            s.commit()

        import lantai.core.acl as acl_mod

        racing = {"done": False}
        real_check = acl_mod.ensure_can_delete

        def _check_with_interleave(principal, **kw):
            out = real_check(principal, **kw)
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory("mem_rb", new_content="并发纠错正文", reason="票23并发")
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with patch("lantai.core.acl.ensure_can_delete", side_effect=_check_with_interleave):
            out = rollback("mem_rb", principal=_P)

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_rb")
            assert out.get("ok") is not True, f"行已并发变化，回滚应拒绝：{out}"
            assert m.content == "并发纠错正文", f"并发纠错被回滚快照覆写成 {m.content!r}"
            assert m.version == 3, f"version={m.version}：纠错 +1 后回滚不得把版本打回 2"


class TestTerminalMergeStale:
    """terminal merge（routes_terminal.py:581-583）：合并按陈旧 version 写回，丢并发纠错。

    注入缝：assert_no_prompt_injection（:577，src/tgt 读取之后、写回 :581-583
    之前）。B 侧完整真实纠错（tgt v1→v2）在缝内提交；A 继续按扫描期
    version=1 写回 2 并整体覆盖正文——B 的 correction 静默消失。修复语义：
    tgt version CAS，冲突则 409（路由契约既有形状）。
    """

    def test_merge_refused_when_target_changed(self, param_env):
        from lantai.api.routes_terminal import MergeReq, merge_memories

        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_src", content="源记忆正文"))
            s.add(_mem("mem_tgt", content="目标记忆正文"))
            s.commit()

        import lantai.security.injection_guard as guard_mod

        racing = {"done": False}
        real_guard = guard_mod.assert_no_prompt_injection

        def _guard_with_interleave(text):
            out = real_guard(text)
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory("mem_tgt", new_content="并发纠错正文", reason="票23并发")
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with (
            patch("lantai.api.routes_terminal.get_session", session_factory),
            patch(
                "lantai.security.injection_guard.assert_no_prompt_injection",
                side_effect=_guard_with_interleave,
            ),
        ):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                merge_memories(MergeReq(source_id="mem_src", target_id="mem_tgt"), principal=_P)
            assert exc_info.value.status_code == 409, (
                f"tgt 已并发变化，合并应 409：{exc_info.value.status_code}"
            )

        with session_factory() as s:
            tgt = s.get(MemoryItem, "mem_tgt")
            corrections = (tgt.provenance or {}).get("corrections") or []
            assert tgt.content == "并发纠错正文", (
                f"tgt 正文被陈旧合并覆写成 {tgt.content!r}（票面 S7 合并臂）"
            )
            assert len(corrections) == 1, "并发纠错的 correction 历史不得丢失"
            assert tgt.version == 2
