"""票 22：裁决/认领状态机零守护——条件 UPDATE（CAS）化与并发回归锁。

病灶（`.scratch/v0.68-next-window-2026-11/issues/22-adjudication-state-machine-races.md`）：
裁决/认领状态机（候选 pending_review、提案 pending/applied、冲突 open、论文
new/retry）全部是「SELECT 快照 → Python 判定 → 无条件 ORM 写回」——pysqlite
惰性 BEGIN 下纯 SELECT 不进写事务，判定与写回之间零隔离：reject 后 approve
可复活已拒候选（S1）、提案可双 apply 双落库（S3）、reject 持旧 ORM 对象可把
已 APPLIED 覆写回 REJECTED（S2）、TTL 扫描可抹掉并发 defer（S5）、冲突可双裁
互覆（S6）、论文批可被双 run 认领（W3）。

修复范式（票面方案 A）：全部改条件 UPDATE——`UPDATE ... WHERE 主键 AND 期望态
+ rowcount 守护`，收敛单一真源 `lantai/storage/cas.py`（既有孤本范式
parameters/service.py 的批准 CAS 同步收敛）。认领放写阶段第一条 DML：惰性
BEGIN 此刻才开写事务，语句对最新已提交状态求值，败方 rowcount=0；胜方回滚
（含异常路径）认领一并撤销，无中间态残留。LLM/embed 网络调用留在认领之前
（先无事务评估、后短事务条件提交）。

测试形态（票面 TDD 要求「真内存库 + 真线程 Barrier 或跨 session 交错注入」）：
选**跨 session 交错注入**。生产竞态窗口全部开在函数体内部（gate 读与写提交
之间），线程 Barrier 无法钉进那里；注入缝选真实外部边界（LLM chat_json /
embed、时钟 utcnow、注册表 get_registry_version、ACL 校验），垫片**先调真
实现，再在返回前执行 B 侧完整真实流程**（第二 session 全真状态变更）——
被测链路零 mock，只把交错点钉死。全程单线程顺序执行：无 sleep、无超时依赖、
无调度抖动，逐例确定性。

替身面声明：chat_json / embed 是外部 LLM 边界（纪律允许替身）；utcnow /
get_registry_version / ensure_can_delete 垫片是「先调真、再注交错」的透传
包装，不替任何被测逻辑；DB、FTS、状态机、apply 全链全真。所有替身都在测试
体内 mock.patch（with 退出即还原，先于 fixture 撕卸）——param_env 内嵌自己的
MonkeyPatch.context 管同一批属性，两层打点靠 fixture 栈还原会因 teardown 顺序
把 param_env 替身写回（conftest 模块绑定护栏判会话红，票 21 同款教训）。
"""

import threading
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from lantai.core.acl import Principal
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal
from lantai.evolution.proposer import propose_from_candidate
from lantai.models.enums import ProposalStatus
from lantai.models.schemas import ProposalDecisionReq
from lantai.models.tables import (
    ConflictEvent,
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryItem,
    MemoryProposal,
    ParamAdvicePaper,
    ParamAdviceRun,
    RawDocument,
)
from lantai.parameters.queue import claim_advice_batch
from lantai.services.candidate_service import (
    CandidateStateConflict,
    defer_candidate,
    review_candidate,
    run_candidate_ttl_once,
)
from lantai.services.conflict_service import resolve_conflict_event
from lantai.services.consolidation_service import run_consolidation_cycle
from lantai.services.evolution_service import decide_proposal


def _cand(cid: str, *, status="pending_review", due=None) -> MemoryCandidate:
    return MemoryCandidate(
        id=cid,
        document_id=f"doc_{cid}",
        summary="大哥偏好饮用浙江新昌明前大佛龙井茶，水温85度",
        claims=["偏好龙井茶"],
        actions=[],
        lane="preference",
        status=status,
        user_id="u_a",
        review_due_at=due,
    )


def _mem_item(mid: str, key: str, content: str) -> MemoryItem:
    return MemoryItem(
        id=mid,
        key=key,
        content=content,
        memory_type="semantic",
        lane="general",
        status="active",
        user_id="u_a",
        decay_score=0.9,
        source_ids=[],
        confidence=0.8,
    )


class TestS1RejectApproveRevival:
    """S1：A reject 提交后 B 的 approve 走到 proposer——必须不产生提案、不复活。

    注入缝：proposer 的 chat_json（秒级 LLM 往返，现状下重取候选零状态检查、
    末尾无条件置 gated——拒绝决定被推翻）。垫片在返回载荷前执行 B 侧真实
    reject（全真 review_candidate，第二 session）。
    """

    def test_approve_after_concurrent_reject_must_not_revive(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_cand("cand_s1"))
            s.commit()

        payload = {
            "proposal_type": "add",
            "memory_type": "semantic",
            "confidence": 0.9,
            "reason": "票 22 并发核验",
        }

        def _llm_with_interleave(_sys_prompt, _user_prompt):
            review_candidate("cand_s1", approve=False, reason="票 22 并发拒绝")
            return dict(payload)

        raised = None
        try:
            with patch("lantai.evolution.proposer.chat_json", side_effect=_llm_with_interleave):
                propose_from_candidate("cand_s1", {"decision": "promote_semantic"})
        except CandidateStateConflict as exc:
            raised = exc

        with session_factory() as s:
            status = s.get(MemoryCandidate, "cand_s1").status
            props = s.exec(select(MemoryProposal)).all()

        assert raised is not None, (
            f"并发败方未被拒绝：approve 继续执行完毕——候选终态 {status!r}、"
            f"落库提案 {len(props)} 条（拒绝被推翻 + 脏提案，票面 S1 现状）"
        )
        assert status == "rejected", f"候选被 approve 复活为 {status!r}"
        assert props == [], "为已拒候选生成了提案"


class _DoubleApplyMixin:
    """S3 公共骨架：A 路 apply 的 embed 缝内，B 路完整真实跑完 apply。"""

    proposal_id = ""
    patch: dict = {}

    def _double_apply(self, session_factory):
        base = lambda texts, **kw: [[0.1] * 8 for _ in texts]  # noqa: E731
        racing = {"active": False}

        def _embed_with_interleave(texts, **kw):
            if racing["active"]:
                return base(texts, **kw)
            racing["active"] = True
            try:
                out = apply_proposal(self.proposal_id)
                assert out.get("ok") is True, f"B 路 apply 应成功：{out}"
            finally:
                racing["active"] = False
            return base(texts, **kw)

        with patch("lantai.evolution.promoter.embed", side_effect=_embed_with_interleave):
            return apply_proposal(self.proposal_id)


class TestS3DoubleApplyAdd(_DoubleApplyMixin):
    """S3 add 分支：双路同提案 apply——库内恰一条 MemoryItem（key 无唯一约束，
    现状两条平行记忆）。注入缝：embed（方案 A 下 embed 在认领之前的无事务
    评估段；现状下同一位置）——B 路在 A 的 embed 返回前完整提交。"""

    proposal_id = "prop_s3a"
    patch = {"memory_type": "semantic", "key": "龙井偏好", "content": "大哥偏好明前大佛龙井茶"}

    def test_double_apply_creates_exactly_one_memory_item(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(
                MemoryProposal(
                    id=self.proposal_id,
                    proposal_type="add",
                    proposed_patch=dict(self.patch),
                    evidence_ids=[],
                    confidence=0.9,
                    status=ProposalStatus.PENDING,
                )
            )
            s.commit()

        out = self._double_apply(session_factory)

        assert out.get("ok") is not True, f"并发败方不得报成功写库：{out}"
        with session_factory() as s:
            items = s.exec(select(MemoryItem).where(MemoryItem.key == "龙井偏好")).all()
            assert len(items) == 1, f"add 双 apply 双落库：{[i.id for i in items]}"
            assert s.get(MemoryProposal, self.proposal_id).status == ProposalStatus.APPLIED


class TestS3DoubleApplyUpdate(_DoubleApplyMixin):
    """S3 update 分支：双路同提案 apply——每个版本号恰一条 checkpoint。

    现状重号的机制：B 路 version 0→1 落 checkpoint(1)；A 路持陈旧快照
    （version=0）写回 0+1=1——数值不变但 checkpoint 第二行同为 version=1，
    编年史重号（票面「version 重号」的本义）。红样钉 checkpoint 行数。
    """

    proposal_id = "prop_s3u"
    patch = {"memory_type": "semantic", "key": "更新目标键", "content": "S3U 新正文"}

    def test_double_apply_bumps_version_exactly_once(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem_item("mem_s3u", "更新目标键", "旧正文"))
            s.add(
                MemoryProposal(
                    id=self.proposal_id,
                    proposal_type="update",
                    target_memory_id="mem_s3u",
                    proposed_patch=dict(self.patch),
                    evidence_ids=[],
                    confidence=0.9,
                    status=ProposalStatus.PENDING,
                )
            )
            s.commit()

        self._double_apply(session_factory)

        with session_factory() as s:
            ckpt_versions = [
                ck.version
                for ck in s.exec(
                    select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == "mem_s3u")
                ).all()
            ]
            assert len(ckpt_versions) == 1, f"update 双 apply checkpoint 重号：{ckpt_versions}"
            assert s.get(MemoryProposal, self.proposal_id).status == ProposalStatus.APPLIED


class TestS2DecideOverwrite:
    """S2：reject 持旧 ORM 对象不得把已 APPLIED 覆写回 REJECTED。

    交错：A=reject 先过状态门（读到 pending），其 decided_at 落值窗口内
    B=approve+apply 完整真实跑完（APPLIED + 内容落库）；A 继续以旧对象写
    REJECTED。红线：内容已写库、账面必须仍是 applied——「内容已写库、
    账面显示拒绝」即脏账。
    """

    def test_concurrent_reject_must_not_rewrite_applied(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem_item("mem_s2", "目标键S2", "旧正文"))
            s.add(
                MemoryProposal(
                    id="prop_s2",
                    proposal_type="update",
                    target_memory_id="mem_s2",
                    proposed_patch={
                        "memory_type": "semantic",
                        "key": "目标键S2",
                        "content": "S2 新正文",
                    },
                    evidence_ids=[],
                    confidence=0.9,
                    status=ProposalStatus.PENDING,
                )
            )
            s.commit()

        from lantai.services import evolution_service

        real_utcnow = evolution_service.utcnow
        racing = {"active": False}

        def _now_with_interleave():
            if racing["active"]:
                return real_utcnow()
            racing["active"] = True
            try:
                out = decide_proposal("prop_s2", ProposalDecisionReq(approve=True))
                assert out.get("ok") is True, f"B 路 approve+apply 应成功：{out}"
            finally:
                racing["active"] = False
            return real_utcnow()

        with (
            patch(
                "lantai.evolution.promoter.embed", lambda texts, **kw: [[0.1] * 8 for _ in texts]
            ),
            patch("lantai.services.evolution_service.utcnow", side_effect=_now_with_interleave),
            pytest.raises(RuntimeError),
        ):
            decide_proposal("prop_s2", ProposalDecisionReq(approve=False, reason="并发拒绝"))

        with session_factory() as s:
            prop = s.get(MemoryProposal, "prop_s2")
            assert prop.status == ProposalStatus.APPLIED, f"APPLIED 被旧对象覆写回 {prop.status!r}"
            assert s.get(MemoryItem, "mem_s2").content == "S2 新正文", "已应用内容不应存在"


class TestS5TtlDeferRace:
    """S5：TTL 超龄扫描 × 用户 defer 并发——defer 审计字段不得被抹掉。

    注入缝：扫描的 utcnow（快照 SELECT 之后、逐行改写之前）——垫片触发
    B 侧真实 defer（第二 session 提交延期四字段）。现状：扫描持快照判
    超龄，无条件改写归档 + _clear_defer_state 整体抹掉新延期。
    """

    def test_ttl_sweep_must_not_wipe_concurrent_defer(self, param_env):
        from lantai.services import candidate_service

        session_factory, _ = param_env
        past = utcnow() - timedelta(days=1)
        with session_factory() as s:
            s.add(_cand("cand_s5", due=past))
            s.commit()

        real_utcnow = candidate_service.utcnow
        racing = {"active": False}

        def _now_with_interleave():
            if racing["active"]:
                return real_utcnow()
            racing["active"] = True
            try:
                out = defer_candidate("cand_s5", 3, "用户延期核验")
                assert out.get("ok") is True, f"B 路 defer 应成功：{out}"
            finally:
                racing["active"] = False
            return real_utcnow()

        with patch("lantai.services.candidate_service.utcnow", side_effect=_now_with_interleave):
            result = run_candidate_ttl_once()

        assert result["archived"] == 0, f"延期中的候选被超龄归档：{result}"
        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_s5")
            assert c.status == "pending_review", f"用户延期中的候选被归档：{c.status!r}"
            assert c.deferred_at is not None, "defer 审计字段被抹掉（deferred_at）"
            assert (c.defer_count or 0) == 1, f"defer 计数被抹掉：{c.defer_count!r}"
            assert c.defer_reason == "用户延期核验", f"defer 理由被抹掉：{c.defer_reason!r}"

    def test_expired_candidate_still_archived(self, param_env):
        """回归锁（现状即绿，如实申报）：真超龄（无并发 defer）仍归档 + 审计字段清空。"""
        session_factory, _ = param_env
        past = utcnow() - timedelta(days=1)
        with session_factory() as s:
            s.add(_cand("cand_s5b", due=past))
            s.commit()

        result = run_candidate_ttl_once()

        assert result["archived"] == 1, f"真超龄候选未被归档：{result}"
        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_s5b")
            assert c.status == "rejected"
            assert c.deferred_at is None and c.review_due_at is None


class TestW3DoubleClaim:
    """W3：论文批双 claim（定时作业 × ingest worker 结尾触发）——每篇恰被
    一个 run 认领。注入缝：claim 的 get_registry_version（扫描/窗口判定之后、
    run 落库之前）——垫片内 B 路完整真实跑完一次 claim_advice_batch。
    """

    def test_double_claim_one_run_per_paper(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            for i in range(1, settings.PARAM_ADVICE_MIN_PAPERS + 1):
                s.add(
                    RawDocument(
                        id=f"raw_w3_{i}",
                        source_type="web",
                        source_id=f"s{i}",
                        url=f"https://example.com/{i}",
                        title=f"论文{i}",
                        content_hash=f"hash_w3_{i}",
                    )
                )
                s.add(
                    ParamAdvicePaper(
                        id=f"pap_w3_{i}",
                        raw_document_id=f"raw_w3_{i}",
                        state="new",
                        available_at=utcnow(),
                    )
                )
            s.commit()

        from lantai.parameters import queue

        real_grv = queue.get_registry_version
        racing = {"active": False}

        def _grv_with_interleave():
            if racing["active"]:
                return real_grv()
            racing["active"] = True
            try:
                other = claim_advice_batch()
                assert other is not None, "B 路认领应成功"
            finally:
                racing["active"] = False
            return real_grv()

        with patch(
            "lantai.parameters.queue.get_registry_version", side_effect=_grv_with_interleave
        ):
            mine = claim_advice_batch()

        assert mine is None, f"并发败方不得带回认领批：{(mine or {}).get('run_id')}"
        with session_factory() as s:
            runs = s.exec(select(ParamAdviceRun)).all()
            papers = s.exec(select(ParamAdvicePaper)).all()
            assert len(runs) == 1, f"双 run 认领同一批论文：{[r.id for r in runs]}"
            for p in papers:
                assert p.state == "processing" and p.run_id == runs[0].id, (
                    f"论文 {p.id} 认领漂移：state={p.state!r} run_id={p.run_id!r}"
                )


class TestS6DoubleAdjudication:
    """S6：同一冲突双裁——先裁者胜，后裁者必须报错而非静默覆盖。

    注入缝：resolve 的 ensure_can_delete（懒导入缝，A 带 admin 身份命中）——
    垫片先调真校验，再执行 B 侧真实裁决（principal=None 内部路径）。
    现状：SELECT-判态-UPDATE 无守护，两人并发裁同一冲突双双 200、后写覆盖。
    """

    def test_first_adjudication_wins_second_conflicts(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem_item("mem_s6", "冲突键S6", "正文S6"))
            s.add(
                ConflictEvent(
                    id="ce_s6",
                    memory_id="mem_s6",
                    incoming_ref="一条新主张",
                    rule_name="mutex",
                    status="open",
                )
            )
            s.commit()

        from lantai.core import acl

        admin = Principal(
            tenant_id=None,
            user_id="u_a",
            agent_id=None,
            session_id=None,
            role="admin",
            allowed_lanes=None,
        )
        real_ensure = acl.ensure_can_delete
        racing = {"active": False}

        def _ensure_with_interleave(*args, **kwargs):
            out = real_ensure(*args, **kwargs)
            if not racing["active"]:
                racing["active"] = True
                try:
                    other = resolve_conflict_event("ce_s6", "dismissed", "B 先裁", principal=None)
                    assert other.get("ok") is True, f"B 路裁决应成功：{other}"
                finally:
                    racing["active"] = False
            return out

        with patch("lantai.core.acl.ensure_can_delete", side_effect=_ensure_with_interleave):
            with pytest.raises(ValueError):
                resolve_conflict_event("ce_s6", "resolved", "A 后裁", principal=admin)

        with session_factory() as s:
            ev = s.get(ConflictEvent, "ce_s6")
            assert ev.status == "dismissed", f"先裁决定被后裁覆盖：{ev.status!r}"
            assert ev.resolved_by == "B 先裁", f"裁决审计被静默替换：{ev.resolved_by!r}"


class TestAmplifierManualTriggerMutex:
    """放大器：手动触发路由必须与定时任务共用互斥，同名重叠即 409。"""

    def test_run_worker_mutex_blocks_second_manual_run(self):
        from lantai.services import worker_operation_service

        release = threading.Event()
        started = threading.Event()

        def blocking_runner():
            started.set()
            release.wait(5)
            return {"ran": True}

        with patch.object(worker_operation_service, "_runners", lambda: {"stub": blocking_runner}):
            t = threading.Thread(target=lambda: worker_operation_service.run_worker("stub"))
            t.start()
            assert started.wait(5), "runner 未启动"
            with pytest.raises(RuntimeError):
                worker_operation_service.run_worker("stub")
            release.set()
            t.join(5)

    def test_evolve_run_route_goes_through_worker_mutex(self):
        """/evolve/run 必须经 run_worker（同名互斥），冲突映射 409。"""
        from lantai.api import routes_evolution

        if not hasattr(routes_evolution, "run_worker"):
            pytest.fail(
                "/evolve/run 未走 run_worker 同名互斥（票 22 放大器：直调 worker 函数绕锁）"
            )

        calls = []
        with patch.object(
            routes_evolution,
            "run_worker",
            side_effect=lambda name: calls.append(name) or {"ok": True, "worker": name},
        ):
            out = routes_evolution.evolve_run_route(principal=None)
        assert calls == ["evolve"], f"/evolve/run 未按 worker 名互斥：{calls}"
        assert out == {"ok": True}, f"响应形状漂移（run_evolve() 契约 {out!r}）"

        with patch.object(
            routes_evolution,
            "run_worker",
            side_effect=RuntimeError("worker is already running"),
        ):
            with pytest.raises(HTTPException) as ei:
                routes_evolution.evolve_run_route(principal=None)
        assert ei.value.status_code == 409, f"互斥冲突未映射 409：{ei.value.status_code}"

    def test_consolidate_route_maps_conflict_to_409(self):
        """/evolution/consolidate 撞上运行中的周期必须 409（周期入口持单飞锁）。"""
        from lantai.api import routes_evolution

        with patch(
            "lantai.services.consolidation_service.run_consolidation_cycle",
            side_effect=RuntimeError("consolidation cycle is already running"),
        ):
            with pytest.raises(HTTPException) as ei:
                routes_evolution.consolidate_run_route(ctx=None)
        assert ei.value.status_code == 409, f"周期互斥冲突未映射 409：{ei.value.status_code}"

    def test_consolidation_cycle_mutex(self):
        """沉潜周期单飞锁：上一周期未结束，第二周期必须冲突（cron × 手动）。"""
        from lantai.services import consolidation_service

        lock = getattr(consolidation_service, "_CYCLE_LOCK", None)
        if lock is None:
            pytest.fail("沉潜周期无单飞互斥（票 22 W2：_CYCLE_LOCK 缺位，cron × 手动可双跑）")

        acquired = lock.acquire(blocking=False)
        assert acquired, "测试自持锁失败"
        try:
            with pytest.raises(RuntimeError):
                run_consolidation_cycle()
        finally:
            lock.release()
