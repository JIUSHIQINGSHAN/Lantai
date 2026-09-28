"""票 `.scratch/proposal-apply-gaps/issues/16-*.md`：提案 apply 全链无归属。

**先说影响**：`apply_proposal` 一个身份都不取，而它的三个寻址字段
（`target_memory_id` / `evidence_ids` / `proposed_patch["key"]`）**全部来自
LLM 输出**。curator 只要回一个别人的记忆 id，apply 就按主键直读然后**改写它**
——deprecate 是归档、merge 证据环是**从 FTS 与向量库除名**、consolidation 是
折叠。这些都没有 undo 入口。且 `evolve_worker.run_evolve_once` /
`run_pending_proposals` 选提案不带归属过滤，**不需要 A 主动调用**——定时任务
会替 A 把提案 apply 到 B 的记忆上。

复现实证（`.scratch/proposal-apply-gaps/probe_16_cross_user_apply.py`，
子进程隔离，五个场景全部成功改写 B 的行）：

```
场景1 update target=B     → B.content 被整条覆盖
场景2 merge evidence=[B]  → B.status=archived + 向量库 delete(B)
场景3 deprecate target=B  → B.status=archived
场景4 update key 回退命中 B → B.content 被整条覆盖
场景5 consolidation evidence 混合 → B.status=consolidated
```

本文件的口径：apply 边界设**一道**硬门，取到 prop 后一次性解析全部目标 id
（target + evidence + key 回退）并统一校验归属；任一条不属于即**整体**拒绝
（`_reject_proposal` 落终态 REJECTED + 留痕），不静默丢弃、不自动改指向。
"整体"是必须的：merge/consolidation 一次改多个目标，逐条补判定就会漏掉
evidence 环那条（票 14 的 rejecter 就是这么漏的），或者把自己的应用了、
把别人的跳过了——半 apply 比不 apply 更脏。

**纪律**：不 mock 被测函数的内部计算。替身只覆盖外部边界——
embed / chat_json（conftest `_stub_external_llm` 与测试体内 patch）与
向量存储（conftest 的 DummyVS）。`apply_proposal` 的分支选择、寻址、
checkpoint、FTS 同步全部真实执行。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.storage import db

A = Principal(tenant_id=None, user_id="u_A", agent_id=None, session_id=None, role="user")
B = Principal(tenant_id=None, user_id="u_B", agent_id=None, session_id=None, role="user")
ADMIN = Principal(tenant_id=None, user_id="u_admin", agent_id=None, session_id=None, role="admin")


@pytest.fixture
def apply_env(monkeypatch):
    """真实内存 SQLite + 真 FTS + patch `db.get_session`。

    替身边界仅外部依赖（embed / chat_json / vector_store 由 conftest 统一处理），
    `apply_proposal` 内部分支与寻址逻辑全部真实执行。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    return engine


def _mem(mid: str, key: str, content: str, user_id: str | None = None, **kw) -> MemoryItem:
    kw.setdefault("status", "active")
    return MemoryItem(
        id=mid,
        key=key,
        content=content,
        memory_type="semantic",
        lane="general",
        importance=0.5,
        user_id=user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
        **kw,
    )


def _prop(pid: str, ptype: str, key: str, content: str, **kw) -> MemoryProposal:
    return MemoryProposal(
        id=pid,
        proposal_type=ptype,
        target_memory_id=kw.pop("target_memory_id", None),
        candidate_id=None,
        evidence_ids=kw.pop("evidence_ids", []),
        reason="ticket16 test",
        proposed_patch={
            "memory_type": "semantic",
            "key": key,
            "content": content,
            "lane": "general",
        },
        confidence=0.8,
        conflict_ids=[],
        status=ProposalStatus.PENDING,
        **kw,
    )


def _row(engine, mid: str) -> dict:
    with Session(engine) as s:
        m = s.get(MemoryItem, mid)
        if m is None:
            return {}
        return {"status": m.status, "content": m.content}


def _fts_findable(engine, mid: str) -> bool:
    """这条记忆还在 FTS 里吗（删索引比改字段更难察觉）。

    按 `memory_id` 列直查而不用 `MATCH`：id 含下划线，FTS5 的默认分词器把
    它拆成多个 token，`MATCH 'mem_B4'` 查的是词不是这一行，恒假。
    """
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT memory_id FROM memory_fts WHERE memory_id = ?", (mid,)
        ).all()
    return any(r[0] == mid for r in rows)


def _seed_fts(engine, mid: str, content: str) -> None:
    """把记忆真实索引进 FTS。

    必须先索引才能断言「还在」——否则被测的恒为 False，断言是空的
    （`_fts_findable` 在一条从未入索引的记忆上恒假，看不出删没删）。
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO memory_fts(memory_id, content) VALUES (:id, :content)"),
            {"id": mid, "content": content},
        )


def _prop_status(engine, pid: str) -> str | None:
    with Session(engine) as s:
        p = s.get(MemoryProposal, pid)
        return None if p is None else p.status


# ── Red 1（决定性）：target 指向 B 的记忆 → 拒绝，B 的落库值不变 ──────


class TestTargetOwnership:
    def test_update_target_of_other_user_is_refused(self, apply_env):
        """A 的 update 提案 target 指向 B 的记忆 → 拒绝 + B 的行一字未动。

        这是本票的核心：apply 是**写**不是读，target 又来自 LLM 输出。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_A1", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B1", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_01", "update", "B的键", "A 覆写后的正文", target_memory_id="mem_B1"))
            s.commit()

        res = apply_proposal("p16_01", principal=A)

        assert res["ok"] is False, "target 指向别人的记忆必须拒绝，不得静默改写"
        assert _row(apply_env, "mem_B1") == {
            "status": "active",
            "content": "B 的原始正文",
        }, "拒绝也必须真的没写进库——落库值一字未动才算拒绝"
        assert _prop_status(apply_env, "p16_01") == ProposalStatus.REJECTED.value, (
            "拒绝须落终态 REJECTED，否则 run_pending_proposals 每轮重试＝livelock"
        )

    def test_deprecate_target_of_other_user_is_refused(self, apply_env):
        """deprecate 是归档（无 undo）——同样不得对别人的记忆生效。"""
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B2", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_02", "deprecate", "B的键", "", target_memory_id="mem_B2"))
            s.commit()

        res = apply_proposal("p16_02", principal=A)

        assert res["ok"] is False
        assert _row(apply_env, "mem_B2")["status"] == "active", "B 不得被 A 的提案归档"

    def test_merge_target_of_other_user_is_refused(self, apply_env):
        """merge 的 else 分支会 `existing.content = content` 整条覆盖。"""
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B3", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_03", "merge", "B的键", "A 的合并正文", target_memory_id="mem_B3"))
            s.commit()

        res = apply_proposal("p16_03", principal=A)

        assert res["ok"] is False
        assert _row(apply_env, "mem_B3")["content"] == "B 的原始正文", "B 的正文不得被覆盖"


# ── Red 2：evidence_ids 含 B 的记忆 → 拒绝，B 仍在 FTS 与向量库 ─────


class TestEvidenceOwnership:
    def test_merge_evidence_of_other_user_is_refused(self, apply_env):
        """merge 证据环会把 B 从 FTS 与向量库**除名**——比改字段更难察觉。

        target 是 A 自己的记忆（这一条会通过），所以断言必须落在
        evidence 那条上：只判 target 不判 evidence，B 照样被删索引。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_A4", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B4", "B的键", "B 的原始正文", "u_B"))
            s.add(
                _prop(
                    "p16_04",
                    "merge",
                    "A的键",
                    "合并后的正文",
                    target_memory_id="mem_A4",
                    evidence_ids=["mem_B4"],
                )
            )
            s.commit()
        _seed_fts(apply_env, "mem_B4", "B 的原始正文")

        res = apply_proposal("p16_04", principal=A)

        assert res["ok"] is False, "evidence 含别人的记忆必须整体拒绝"
        assert _row(apply_env, "mem_B4")["status"] == "active", "B 不得被归档"
        assert _fts_findable(apply_env, "mem_B4"), "B 不得被从 FTS 除名"

    def test_add_evidence_of_other_user_is_refused(self, apply_env):
        """add 分支会给每条 evidence 建 supports 边——边指向别人的记忆即脏写。

        （票 17 修过边的读侧归属；这里是写侧：不该为别人的记忆建边。）
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B5", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_05", "add", "新键", "新正文", evidence_ids=["mem_B5"]))
            s.commit()

        res = apply_proposal("p16_05", principal=A)

        assert res["ok"] is False, "add 提案的 evidence 指向别人的记忆也必须拒绝"
        with Session(apply_env) as s:
            edges = s.exec(select(MemoryEdge)).all()
        assert not edges, (
            f"不得为别人的记忆建边：{[(e.source_memory_id, e.target_memory_id) for e in edges]}"
        )


# ── Red 3：key 回退只命中 B 的记忆 → 拒绝（不新建平行记忆）──────────


class TestKeyFallbackOwnership:
    def test_key_fallback_to_other_user_is_refused(self, apply_env):
        """`_resolve_update_target` 的 key 回退无归属过滤。

        库里只有 B 的一条记忆用这个 key → 回退解析到 B 的记忆上，
        而 `prop.target_memory_id` 为空，单看 target 字段判不出问题。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B6", "唯一键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_06", "update", "唯一键", "A 覆写后的正文"))
            s.commit()

        res = apply_proposal("p16_06", principal=A)

        assert res["ok"] is False, "key 回退解析到别人的记忆必须拒绝"
        assert _row(apply_env, "mem_B6")["content"] == "B 的原始正文", "B 的正文不得被覆盖"
        with Session(apply_env) as s:
            new_items = [m for m in s.exec(select(MemoryItem)).all() if m.id != "mem_B6"]
        assert not new_items, f"不得降级为 add 新建平行记忆：{[m.id for m in new_items]}"


# ── Red 4：混合目标 → 整体拒绝（不能半 apply）──────────────────────


class TestMixedTargetsRejectedWholesale:
    def test_mixed_targets_are_refused_wholesale(self, apply_env):
        """consolidation 的 evidence 一条自己 + 一条 B 的 → **整体**拒绝。

        这是"一道硬门"而不是"逐个 session.get 后补判定"的决定性用例：
        逐条补判定时，A 自己的那条会先被折叠（已写库），B 的那条被跳过——
        半 apply 比不 apply 更脏（A 的记忆已经被折叠且没有 undo）。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_A7", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B7", "B的键", "B 的正文", "u_B"))
            s.add(
                _prop(
                    "p16_07",
                    "consolidation",
                    "巩固主记忆",
                    "折叠后的主记忆正文",
                    evidence_ids=["mem_A7", "mem_B7"],
                )
            )
            s.commit()

        res = apply_proposal("p16_07", principal=A)

        assert res["ok"] is False, "混合目标必须整体拒绝"
        assert _row(apply_env, "mem_A7")["status"] == "active", (
            "自己的那条也不得被折叠——半 apply 比不 apply 更脏"
        )
        assert _row(apply_env, "mem_B7")["status"] == "active", "B 的不得被折叠"
        with Session(apply_env) as s:
            masters = [
                m for m in s.exec(select(MemoryItem)).all() if m.content == "折叠后的主记忆正文"
            ]
        assert not masters, "整体拒绝即不得新建主记忆（否则折叠已发生）"


# ── Red 5 / 6 / 7：自己的提案照常、NULL 属主照常、admin 全量 ────────


class TestLegitimatePathsStillWork:
    def test_own_proposal_still_applies(self, apply_env):
        """Red 5：A 自己的提案必须照常 apply（不能把功能修废）。"""
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_A8", "A的键", "A 的旧正文", "u_A"))
            s.add(
                _prop(
                    "p16_08",
                    "update",
                    "A的键",
                    "A 的新正文",
                    target_memory_id="mem_A8",
                    evidence_ids=["mem_A8"],
                )
            )
            s.commit()

        res = apply_proposal("p16_08", principal=A)

        assert res["ok"] is True, f"A 自己的提案必须照常 apply：{res}"
        assert _row(apply_env, "mem_A8")["content"] == "A 的新正文"
        assert _prop_status(apply_env, "p16_08") == ProposalStatus.APPLIED.value

    def test_null_owner_memory_still_applies(self, apply_env):
        """Red 6：NULL 属主老行必须照常可被 apply。

        真实库 636/657 行 memoryitem 是 `user_id IS NULL`——判不可见会让
        单人部署整体空转（同前 14 票口径：NULL 是「未记录」不是「属于所有人」）。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_null", "老键", "老正文", None))
            s.add(_prop("p16_09", "update", "老键", "新正文", target_memory_id="mem_null"))
            s.commit()

        res = apply_proposal("p16_09", principal=A)

        assert res["ok"] is True, f"NULL 属主老行必须照常可被 apply：{res}"
        assert _row(apply_env, "mem_null")["content"] == "新正文"

    def test_admin_applies_to_any_user(self, apply_env):
        """Red 7a：admin 全量。"""
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B10", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_10", "update", "B的键", "admin 改的正文", target_memory_id="mem_B10"))
            s.commit()

        res = apply_proposal("p16_10", principal=ADMIN)

        assert res["ok"] is True, f"admin 必须全量：{res}"
        assert _row(apply_env, "mem_B10")["content"] == "admin 改的正文"

    def test_principal_none_applies_to_any_user(self, apply_env):
        """Red 7b：`principal=None`（worker/CLI/scheduler）全量。

        同前 14 票口径——收窄成空转会让演化与遗忘整体停摆。
        """
        from lantai.evolution.promoter import apply_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_B11", "B的键", "B 的原始正文", "u_B"))
            s.add(_prop("p16_11", "update", "B的键", "worker 改的正文", target_memory_id="mem_B11"))
            s.commit()

        res = apply_proposal("p16_11")

        assert res["ok"] is True, f"principal=None 必须全量：{res}"
        assert _row(apply_env, "mem_B11")["content"] == "worker 改的正文"


# ── Red 8（可达性）：worker 路径也不得跨用户 apply ─────────────────


class TestWorkerReachability:
    def test_run_pending_proposals_does_not_apply_other_users(self, apply_env):
        """Red 8：定时任务替 A 把提案 apply 到 B 的记忆上——这条必须断。

        `run_pending_proposals` 专捞 `status == APPROVED` 的提案，一个
        归属过滤都不带。修好 apply 边界后，worker 这条路仍要单独收窄
        （口径同票 12 的 `_kaogong_scope`）——否则路由层修得再好，
        定时任务照样跨用户 apply。

        **为什么断言「选中的提案集」而不是「B 的记忆有没有被改」**：提案的
        target 指向 B 的记忆，所以 apply 边界的硬门本来就会拒它——只看落库值
        时，把 worker 的 scope 改回 None 测试照样绿（硬门兜住了）。要杀掉那个
        变异，必须观察到 **B 的提案根本没进候选集**。spy 记下 worker 实际
        执行了哪些提案 id。
        """
        from lantai.workers import evolve_worker
        from lantai.workers.evolve_worker import run_pending_proposals

        executed: list[str] = []
        real_apply = evolve_worker.apply_proposal

        def spy_apply(pid, **kw):
            executed.append(pid)
            return real_apply(pid, **kw)

        with Session(apply_env) as s:
            s.add(_mem("mem_B12", "B的键", "B 的原始正文", "u_B"))
            # B 的已批准提案（A 的视角看不到它，也不该被执行）
            s.add(
                _prop(
                    "p16_12",
                    "update",
                    "B的键",
                    "B 的新正文",
                    target_memory_id="mem_B12",
                    user_id="u_B",
                )
            )
            p = s.get(MemoryProposal, "p16_12")
            p.status = ProposalStatus.APPROVED
            s.add(p)
            s.commit()

        import unittest.mock as mock

        with mock.patch.object(evolve_worker, "apply_proposal", spy_apply):
            run_pending_proposals(principal=A)

        assert "p16_12" not in executed, f"worker 的候选集不得含 B 的提案（实际执行了 {executed}）"
        assert _row(apply_env, "mem_B12")["content"] == "B 的原始正文", "B 的记忆不得被改写"

    def test_run_evolve_once_does_not_touch_other_users_candidates(self, apply_env):
        """Red 8 的另一半：`run_evolve_once` 的候选集也不能跨用户。

        它按 `status in ('new','fastpath')` 全表捞候选，然后
        `decide → propose_from_candidate → apply_proposal` 一路到底。
        """
        from lantai.models.tables import MemoryCandidate
        from lantai.workers.evolve_worker import run_evolve_once

        with Session(apply_env) as s:
            s.add(_mem("mem_B13", "B的键", "B 的原始正文", "u_B"))
            s.add(
                MemoryCandidate(
                    id="cand_B",
                    document_id="doc_B",
                    summary="B 的候选",
                    claims=[],
                    actions=[],
                    lane="general",
                    status="new",
                    user_id="u_B",
                )
            )
            s.commit()

        run_evolve_once(principal=A)

        assert _row(apply_env, "mem_B13")["content"] == "B 的原始正文", "worker 不得处理别人的候选"


# ── 生成侧：LLM 输出是不可信输入，产出 target/evidence 时就要过滤 ────


class TestProposalGenerationFiltersOwnership:
    def test_proposer_discards_target_of_other_user(self, apply_env, monkeypatch):
        """`propose_from_candidate` 的 target_key 解析必须带归属过滤。

        LLM 按 PROPOSAL_SYS 返回 target_key，proposer 按它查 active 记忆。
        库里只有 B 的一条用这个 key → 解析到 B 的记忆 → 提案的
        target_memory_id 钉在别人的记忆上，将来 apply 即越权写。
        宁 miss 不脏写：整条丢弃，不落提案。
        """
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate
        from lantai.models.tables import MemoryCandidate

        with Session(apply_env) as s:
            s.add(_mem("mem_B14", "唯一键", "B 的原始正文", "u_B"))
            s.add(
                MemoryCandidate(
                    id="cand_A",
                    document_id="doc_A",
                    summary="A 的候选",
                    claims=[],
                    actions=[],
                    lane="general",
                    status="new",
                    user_id="u_A",
                )
            )
            s.commit()

        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": "update",
                "target_key": "唯一键",
                "new_content": "A 的新正文",
                "memory_type": "semantic",
                "reason": "value changed",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_A", {"decision": "promote_semantic"}, principal=A)

        assert prop is None, "target_key 只命中别人的记忆必须整条丢弃（宁 miss 不脏写）"
        with Session(apply_env) as s:
            assert s.exec(select(MemoryProposal)).all() == [], "丢弃即不落提案行"

    def test_reflector_discards_evidence_of_other_user(self, apply_env):
        """`propose_from_reflection` 的 evidence 存在性校验必须同时校验归属。

        它现在只查 `session.get(MemoryItem, e) is not None`——库里存在
        不等于属于当前主体。curator 回一个别人的 id，这条正文就进了提案的
        evidence_ids，apply 时即被删索引。
        """
        from lantai.evolution.reflector import propose_from_reflection

        with Session(apply_env) as s:
            s.add(_mem("mem_A15", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B15", "B的键", "B 的正文", "u_B"))
            s.commit()

        props = propose_from_reflection(
            Session(apply_env),
            [{"memory_id": "mem_A15", "key": "A的键", "content": "A 的正文", "lane": "general"}],
            {
                "proposals": [
                    {
                        "proposal_type": "update",
                        "target_memory_id": "mem_A15",
                        "evidence_ids": ["mem_A15", "mem_B15"],
                        "new_content": "新正文",
                        "confidence": 0.9,
                        "reason": "r",
                    }
                ]
            },
            principal=A,
        )

        for p in props:
            assert "mem_B15" not in (p.evidence_ids or []), (
                "evidence 不得含别人的记忆 id——apply 时它会被删索引"
            )

    def test_reflector_discards_target_of_other_user(self, apply_env):
        """`propose_from_reflection` 的 target 归属校验（M9 的断言缺口）。

        上一条只把别人的 id 放在 evidence 里，target 仍是 A 自己的——所以
        「target 不判归属」这个变异杀不掉。这条把 target 也换成 B 的：
        curator 回一个别人的 target_memory_id，整条提案必须不生成。

        （target 与 evidence 是两条独立路径：前者经 `session.get` 主键直读，
        后者经 `id.in_` + scope。各自的变异要各自的输入才杀得掉。）
        """
        from lantai.evolution.reflector import propose_from_reflection

        with Session(apply_env) as s:
            s.add(_mem("mem_A16", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B16", "B的键", "B 的正文", "u_B"))
            s.commit()

        props = propose_from_reflection(
            Session(apply_env),
            [
                {"memory_id": "mem_A16", "key": "A的键", "content": "A 的正文", "lane": "general"},
                {"memory_id": "mem_B16", "key": "B的键", "content": "B 的正文", "lane": "general"},
            ],
            {
                "proposals": [
                    {
                        "proposal_type": "update",
                        "target_memory_id": "mem_B16",
                        "evidence_ids": ["mem_A16"],
                        "new_content": "新正文",
                        "confidence": 0.9,
                        "reason": "r",
                    }
                ]
            },
            principal=A,
        )

        assert props == [], f"target 指向别人的记忆必须整条丢弃，却生成了 {[p.id for p in props]}"
        with Session(apply_env) as s:
            assert s.exec(select(MemoryProposal)).all() == [], "丢弃即不落提案行"


# ── service 层：approve 会 apply_proposal 直接写库，principal 必须下传 ──


class TestServiceApproveCarriesPrincipal:
    def test_approve_of_cross_target_proposal_is_refused(self, apply_env):
        """`decide_proposal(approve=True)` → `apply_proposal` 必须带 principal。

        approve 是**破坏性操作**（票 readside-gaps/02 已修「A 能裁 B 的
        提案」）：即使 A 裁的是自己的提案，提案的 target 仍可能指向 B 的
        记忆（三个寻址字段全部来自 LLM 输出）。此时 apply 若不带 principal，
        路由层的归属校验就被绕过了——`/proposals/{id}/decide` 是普通用户
        最容易走到的路径。
        """
        from lantai.models.schemas import ProposalDecisionReq
        from lantai.services.evolution_service import decide_proposal

        with Session(apply_env) as s:
            s.add(_mem("mem_A17", "A的键", "A 的正文", "u_A"))
            s.add(_mem("mem_B17", "B的键", "B 的原始正文", "u_B"))
            # A 自己的提案，但 target 指向 B 的记忆（LLM 输出就是这种形状）
            s.add(
                _prop(
                    "p16_17",
                    "update",
                    "B的键",
                    "A 覆写后的正文",
                    target_memory_id="mem_B17",
                    user_id="u_A",
                )
            )
            s.commit()

        res = decide_proposal(
            "p16_17", ProposalDecisionReq(approve=True, reason="approve it"), principal=A
        )

        assert res.get("ok") is False, f"approve 一条指向 B 的记忆的提案必须被拒：{res}"
        assert _row(apply_env, "mem_B17")["content"] == "B 的原始正文", "B 的正文不得被覆盖"
