"""提案目标寻址硬门（票据 `.scratch/proposal-target-gap/issues/02-*.md`）。

背景（实证 `.scratch/proposal-target-gap/probe_resolution.py`，真实开发库）：
72 条非 add 提案中 **31 条（43%）目标寻址失败后静默落入 add 分支新建平行记忆**
（新旧矛盾并存），**17 条（24%）key 多义时任取其一**，只有 24 条（33%）行为正确。
根因：`proposer` 从不回填 `target_memory_id`（LLM 返回的是 `target_key`），
`promoter.apply_proposal` 的 key 回退既无 unique 保证也无多义拒绝。

本文件的硬门口径（ADR-0050 决策 3 stale 硬门同款）：
update / merge / deprecate 提案若解析不到**唯一** active 目标 →
显式拒绝（`ok=False` + REJECTED + decision_reason + decided_at），
**不新建 MemoryItem**（宁 miss 不脏写）。

替身边界：仅 embed 与外部向量库（conftest 的 `param_env` 已统一处理），
`apply_proposal` 内部分支与寻址逻辑全部真实执行。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryItem, MemoryProposal


def _mem(mid: str, key: str, content: str, **kw) -> MemoryItem:
    kw.setdefault("status", "active")
    return MemoryItem(
        id=mid,
        key=key,
        content=content,
        lane="general",
        importance=0.5,
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
        reason="target-gate test",
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


class TestTargetResolutionHardGate:
    """目标不可寻址的非 add 提案必须被显式拒绝，且不得新建记忆。"""

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_key_matches_nothing_refuses_without_creating(self, param_env, ptype):
        """key 在库中无任何 active 匹配 → 拒绝 + 零新 MemoryItem（禁落入 add 分支）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_other", "别的键", "无关内容"))
            s.add(_prop("prop_gate_01", ptype, "不存在的键", "提案正文"))
            s.commit()

            res = apply_proposal("prop_gate_01")

        assert res["ok"] is False, "目标寻址失败必须显式拒绝，不得静默落入 add 分支"
        assert "target" in res["reason"] or "目标" in res["reason"]

        with sf() as s:
            # 零新记忆：库中仍只有 seed 的那一条
            rows = s.exec(select(MemoryItem)).all()
            assert len(rows) == 1, f"拒绝后不得新建 MemoryItem，实得 {[r.id for r in rows]}"
            assert rows[0].id == "mem_other"
            # 提案落终态 REJECTED + 留痕（同 stale 硬门口径）
            p = s.get(MemoryProposal, "prop_gate_01")
            assert p.status == ProposalStatus.REJECTED
            assert p.decision_reason, "拒绝必须留痕（宁 miss 不脏写）"
            assert p.decided_at is not None, "拒绝即系统裁决时刻（ADR-0053）"

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_ambiguous_key_refuses_and_lists_candidates(self, param_env, ptype):
        """key 被多条 active 记忆共用 → 拒绝并列候选 id，不得任取其一。

        `MemoryItem.key` 无 unique 约束（tables.py:111 仅 index=True），
        实证库内 145 个 active key 中 3 个被多条共用。
        """
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_dup_a", "歧义键", "版本 A"))
            s.add(_mem("mem_dup_b", "歧义键", "版本 B"))
            s.add(_prop("prop_gate_02", ptype, "歧义键", "提案正文"))
            s.commit()

            res = apply_proposal("prop_gate_02")

        assert res["ok"] is False, "key 多义必须拒绝，不得任取其一静默改错目标"
        with sf() as s:
            rows = s.exec(select(MemoryItem)).all()
            assert len(rows) == 2, "拒绝后不得新建 MemoryItem"
            # 两条歧义目标都保持原状（未被改内容/未被折叠）
            assert s.get(MemoryItem, "mem_dup_a").content == "版本 A"
            assert s.get(MemoryItem, "mem_dup_b").content == "版本 B"
            assert s.get(MemoryItem, "mem_dup_a").status == "active"
            assert s.get(MemoryItem, "mem_dup_b").status == "active"
            p = s.get(MemoryProposal, "prop_gate_02")
            assert p.status == ProposalStatus.REJECTED
            assert p.decision_reason, "拒绝必须留痕"
            # 候选 id 如实列出，供人工裁决（不猜）
            detail = res.get("detail") or {}
            cands = set(detail.get("candidates") or [])
            assert cands == {"mem_dup_a", "mem_dup_b"}, f"须列出全部候选 id，实得 {cands}"

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_target_points_to_non_active_refuses(self, param_env, ptype):
        """target_memory_id 指向 archived 记忆 → 拒绝（不重建、不复活）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_arch", "已归档", "旧内容", status="archived"))
            s.add(
                _prop(
                    "prop_gate_03",
                    ptype,
                    "已归档",
                    "提案正文",
                    target_memory_id="mem_arch",
                )
            )
            s.commit()

            res = apply_proposal("prop_gate_03")

        assert res["ok"] is False, "目标非 active 必须拒绝，不得把归档记忆改回 active"
        with sf() as s:
            arch = s.get(MemoryItem, "mem_arch")
            assert arch.status == "archived", "不得复活已归档目标"
            assert arch.content == "旧内容", "不得改写非 active 目标"
            assert s.exec(select(MemoryItem)).all().__len__() == 1, "不得新建 MemoryItem"
            p = s.get(MemoryProposal, "prop_gate_03")
            assert p.status == ProposalStatus.REJECTED

    def test_missing_target_id_refuses(self, param_env):
        """target_memory_id 指向不存在的行 → 拒绝（幽灵目标不得落入 add）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(
                _prop("prop_gate_04", "update", "某个键", "提案正文", target_memory_id="mem_ghost")
            )
            s.commit()

            res = apply_proposal("prop_gate_04")

        assert res["ok"] is False
        with sf() as s:
            assert s.exec(select(MemoryItem)).all() == [], "幽灵目标不得产出新记忆"
            assert s.get(MemoryProposal, "prop_gate_04").status == ProposalStatus.REJECTED

    def test_rejected_proposal_is_idempotent_on_reapply(self, param_env):
        """拒绝后重复 apply 仍 ok=False，且不重复落 decided_at（防 livelock）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_prop("prop_gate_05", "update", "不存在", "提案正文"))
            s.commit()

            first = apply_proposal("prop_gate_05")
            assert first["ok"] is False
            with sf() as s:
                decided_at_1 = s.get(MemoryProposal, "prop_gate_05").decided_at

            second = apply_proposal("prop_gate_05")
            assert second["ok"] is False

        with sf() as s:
            p = s.get(MemoryProposal, "prop_gate_05")
            assert p.decided_at == decided_at_1, "重复 apply 不得改写 decided_at"
            assert s.exec(select(MemoryItem)).all() == [], "仍不得新建 MemoryItem"


class TestTargetResolutionHappyPaths:
    """硬门不得误伤可正常寻址的提案（回归护栏）。"""

    def test_explicit_target_id_updates_in_place(self, param_env):
        """target_memory_id 命中 active → 正常 update（原地更新，不新建）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_t1", "键一", "旧内容"))
            s.add(
                _prop(
                    "prop_ok_01",
                    "update",
                    "键一",
                    "新内容",
                    target_memory_id="mem_t1",
                    evidence_ids=["mem_t1"],
                )
            )
            s.commit()

            res = apply_proposal("prop_ok_01")

        assert res["ok"] is True
        with sf() as s:
            rows = s.exec(select(MemoryItem)).all()
            assert len(rows) == 1, "update 必须原地改，不得新建平行记忆"
            assert rows[0].content == "新内容"
            assert rows[0].version == 2
            assert s.get(MemoryProposal, "prop_ok_01").status == ProposalStatus.APPLIED

    def test_unique_key_fallback_still_resolves(self, param_env):
        """key 唯一命中 active（target 为空）→ 仍按 key 回退解析并原地更新。

        向后兼容：历史提案里确有靠 key 回退跑对的情况（实证 3/72），
        硬门只拒「解析不到/多义」，不把唯一命中也一起拒掉。
        """
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_t2", "唯一键", "旧内容"))
            s.add(_prop("prop_ok_02", "update", "唯一键", "新内容", evidence_ids=["mem_t2"]))
            s.commit()

            res = apply_proposal("prop_ok_02")

        assert res["ok"] is True
        with sf() as s:
            rows = s.exec(select(MemoryItem)).all()
            assert len(rows) == 1
            assert rows[0].content == "新内容"
            # 解析结果应回填到提案上（留痕，供审计追溯本次寻址）
            p = s.get(MemoryProposal, "prop_ok_02")
            assert p.target_memory_id == "mem_t2", "key 回退解析成功须回填 target_memory_id"

    def test_add_proposal_unaffected_by_gate(self, param_env):
        """add 提案本就不该有目标，硬门不得误伤（照旧新建 + supports 边）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_ev", "证据", "证据内容"))
            s.add(_prop("prop_ok_03", "add", "新键", "新记忆内容", evidence_ids=["mem_ev"]))
            s.commit()

            res = apply_proposal("prop_ok_03")

        assert res["ok"] is True
        with sf() as s:
            rows = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
            assert len(rows) == 2, "add 提案照旧新建记忆"
            new = [r for r in rows if r.id != "mem_ev"][0]
            assert new.key == "新键"

    def test_deprecate_with_valid_target_archives_it(self, param_env):
        """deprecate + 合法 target → 归档目标（deprecate 主路径回归）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_t3", "待废弃", "旧内容"))
            s.add(
                _prop(
                    "prop_ok_04",
                    "deprecate",
                    "待废弃",
                    "",
                    target_memory_id="mem_t3",
                    evidence_ids=["mem_t3"],
                )
            )
            s.commit()

            res = apply_proposal("prop_ok_04")

        assert res["ok"] is True
        with sf() as s:
            assert s.get(MemoryItem, "mem_t3").status == "archived"
            assert s.exec(select(MemoryItem)).all().__len__() == 1, "不得新建平行记忆"


class TestDecidedAtIsAwareUtc:
    """拒绝落时刻必须 aware（sqlmodel 0.0.47 起 naive datetime 写库直接被拒）。"""

    def test_rejection_decided_at_is_aware(self, param_env):
        sf, _ = param_env
        with sf() as s:
            s.add(_prop("prop_gate_06", "update", "不存在", "提案正文"))
            s.commit()
            before = utcnow()
            res = apply_proposal("prop_gate_06")
            after = utcnow() + timedelta(seconds=5)

        assert res["ok"] is False
        with sf() as s:
            decided_at = s.get(MemoryProposal, "prop_gate_06").decided_at
            assert decided_at is not None
            # 只锁时刻不锁时区标注（读侧 tzinfo 随 sqlmodel 版本变）
            moment = decided_at if decided_at.tzinfo else decided_at.replace(tzinfo=UTC)
            assert before <= moment <= after


# ── 票 01：proposer 侧目标寻址 ────────────────────────────────────────
# 背景：LLM 按 PROPOSAL_SYS 返回 target_key，而 MemoryProposal 寻址读
# target_memory_id——不解析则非 add 提案的 target 恒为 NULL（实证真实库
# 72 条非 add 提案中 51 条无 target）。口径对齐 reflector.propose_from_reflection：
# 解析不到唯一 active 目标 → 整条丢弃，不降级为 add。


@pytest.fixture
def proposer_env(monkeypatch):
    """内存 SQLite + 真 FTS + patch db.get_session（替身边界仅外部依赖）。

    `chat_json` 不在此处替——conftest 的 autouse `_stub_external_llm` 已统一处理
    （proposer 的 chat_json 绑定在其清单内），且测试体内自行 patch 特定返回值
    晚于该 fixture 生效（既有范式）。
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


class TestProposerTargetResolution:
    """propose_from_candidate 必须把 target_key 解析为 target_memory_id。"""

    @staticmethod
    def _seed_cand(engine, cand_id="cand_tg"):
        from lantai.models.tables import MemoryCandidate

        with Session(engine) as s:
            s.add(
                MemoryCandidate(
                    id=cand_id,
                    document_id=f"doc_{cand_id}",
                    summary="候选摘要",
                    claims=["声明"],
                    actions=[],
                    lane="general",
                    status="new",
                )
            )
            s.commit()

    @staticmethod
    def _seed_mem(engine, mid, key, content, status="active"):
        from lantai.core.time import utcnow
        from lantai.models.tables import MemoryItem

        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id=mid,
                    key=key,
                    content=content,
                    lane="general",
                    status=status,
                    importance=0.5,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            s.commit()

    def test_add_proposal_gets_no_target(self, proposer_env):
        """add 提案不该有 target（本就不寻址）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": "add",
                "target_key": "",
                "new_content": "新记忆",
                "memory_type": "semantic",
                "reason": "new fact",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is not None, "add 提案必须正常生成"
        assert prop.target_memory_id is None
        assert prop.proposal_type == "add"
        with Session(proposer_env) as s:
            assert s.get(MemoryProposal, prop.id) is not None, "add 提案须落库"

    def test_update_resolves_unique_key_to_id(self, proposer_env):
        """update + target_key 唯一命中 → target_memory_id 落真实 id。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_mem(proposer_env, "mem_u1", "目标键", "旧内容")
        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": "update",
                "target_key": "目标键",
                "new_content": "新内容",
                "memory_type": "semantic",
                "reason": "value changed",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is not None
        assert prop.target_memory_id == "mem_u1", "target_key 必须解析为真实 id"
        assert prop.proposal_type == "update"

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_unresolvable_target_discards_proposal(self, proposer_env, ptype):
        """target_key 解析不到 active 记忆 → 整条丢弃（返回 None，不落库）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": ptype,
                "target_key": "库里没有的键",
                "new_content": "新内容",
                "memory_type": "semantic",
                "reason": "x",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is None, f"{ptype} 目标不可寻址必须整条丢弃，不得降级为 add"
        with Session(proposer_env) as s:
            assert s.exec(select(MemoryProposal)).all() == [], "丢弃即不落提案行"

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_ambiguous_target_discards_proposal(self, proposer_env, ptype):
        """target_key 被多条 active 记忆共用 → 整条丢弃（不任取其一）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_mem(proposer_env, "mem_a1", "歧义键", "版本 A")
        self._seed_mem(proposer_env, "mem_a2", "歧义键", "版本 B")
        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": ptype,
                "target_key": "歧义键",
                "new_content": "新内容",
                "memory_type": "semantic",
                "reason": "x",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is None, "key 多义必须丢弃，不得任取其一静默改错目标"

    @pytest.mark.parametrize("ptype", ["update", "merge", "deprecate"])
    def test_empty_target_key_discards_proposal(self, proposer_env, ptype):
        """非 add 提案的 target_key 为空 → 整条丢弃（LLM 漏填不得猜）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_mem(proposer_env, "mem_e1", "某个键", "内容")
        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": ptype,
                "target_key": "",
                "new_content": "新内容",
                "memory_type": "semantic",
                "reason": "x",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is None, "target_key 为空必须丢弃（宁 miss 不脏写）"

    def test_target_pointing_to_archived_discards(self, proposer_env):
        """target_key 只命中 archived 记忆 → 丢弃（不得把归档记忆当目标）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_mem(proposer_env, "mem_arch1", "已归档键", "旧", status="archived")
        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": "update",
                "target_key": "已归档键",
                "new_content": "新",
                "memory_type": "semantic",
                "reason": "x",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is None, "archived 目标不得被寻址（不复活、不改写非 active 记忆）"


class TestProposalTypeWhitelist:
    """未知 proposal_type 必须被拒，不得落入 add 分支新建平行记忆。

    背景：`apply_proposal` 末行 `elif prop.proposal_type == "add" or not existing:`
    的兜底会吞掉任何未知类型——新建记忆 + trigger 记 gate + 多出 supports 边，
    而提案本想做的事一件没做。`proposer` 侧同样无白名单（reflector 侧有：
    `_VALID_TYPES` + `:250` 的 continue），两条产出链路一条校验一条不校验。
    """

    BOGUS_TYPES = ["split", "link", "refine", "Add", "ADD", "updates", ""]

    @staticmethod
    def _seed_cand(engine, cand_id="cand_tg"):
        from lantai.models.tables import MemoryCandidate

        with Session(engine) as s:
            s.add(
                MemoryCandidate(
                    id=cand_id,
                    document_id=f"doc_{cand_id}",
                    summary="候选摘要",
                    claims=["声明"],
                    actions=[],
                    lane="general",
                    status="new",
                )
            )
            s.commit()

    @pytest.mark.parametrize("bogus", BOGUS_TYPES)
    def test_unknown_type_refuses_without_creating(self, param_env, bogus):
        """未知类型 → 显式拒绝 + 零新 MemoryItem（禁落入 add 分支）。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_seed", "种子键", "种子内容"))
            s.add(_prop("prop_bogus", bogus, "某个键", "提案正文"))
            s.commit()

            res = apply_proposal("prop_bogus")

        assert res["ok"] is False, f"未知提案类型 {bogus!r} 必须显式拒绝，不得落入 add 分支"
        reason = res["reason"]
        assert "type" in reason.lower() or "类型" in reason, (
            f"拒绝理由须点明是类型问题，实得: {reason}"
        )

        with sf() as s:
            rows = s.exec(select(MemoryItem)).all()
            assert len(rows) == 1, f"拒绝后不得新建 MemoryItem，实得 {[r.id for r in rows]}"
            assert rows[0].id == "mem_seed"
            p = s.get(MemoryProposal, "prop_bogus")
            assert p.status == ProposalStatus.REJECTED
            assert p.decision_reason, "拒绝必须留痕（宁 miss 不脏写）"
            assert p.decided_at is not None

    @pytest.mark.parametrize("ptype", ["add", "update", "merge", "deprecate"])
    def test_known_types_not_refused_by_whitelist(self, param_env, ptype):
        """白名单闸不得误伤合法类型（回归护栏：行为与设闸前一致）。

        add 正常新建；update/merge/deprecate 的目标在本用例中可唯一寻址，
        故同样应成功——证明拒绝只发生在类型不认识时，而非闸门本身写错。
        """
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_known", "已知键", "已知内容"))
            s.add(
                _prop(
                    "prop_known",
                    ptype,
                    "已知键",
                    "新正文",
                    target_memory_id="mem_known",
                    evidence_ids=[],
                )
            )
            s.commit()

            res = apply_proposal("prop_known")

        assert res.get("ok") is True, f"合法类型 {ptype} 不得被白名单误伤：{res}"
        assert "type" not in str(res.get("reason", "")).lower() or res["ok"]

    def test_consolidation_still_reaches_its_branch(self, param_env):
        """consolidation 是白名单成员（consolidation_service 直写），不得被拒。"""
        sf, _ = param_env
        with sf() as s:
            s.add(_mem("mem_master_src", "来源键", "来源内容"))
            s.add(
                _prop(
                    "prop_cons",
                    "consolidation",
                    "主记忆键",
                    "主记忆正文",
                    evidence_ids=["mem_master_src"],
                )
            )
            s.commit()

            res = apply_proposal("prop_cons")

        assert res.get("ok") is True, f"consolidation 不得被白名单拒绝：{res}"
        assert res.get("folded") == 1, "consolidation 应折叠 1 条碎片（未被降级为 add）"

    @pytest.mark.parametrize("bogus", ["split", "link", "Add"])
    def test_proposer_discards_unknown_type(self, proposer_env, bogus):
        """proposer 侧：LLM 返回未知类型 → 整条不生成（不降级为 add）。"""
        from unittest.mock import patch

        from lantai.evolution.proposer import propose_from_candidate

        self._seed_cand(proposer_env)
        with patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": bogus,
                "target_key": "",
                "new_content": "某些内容",
                "memory_type": "semantic",
                "reason": "x",
                "confidence": 0.8,
            },
        ):
            prop = propose_from_candidate("cand_tg", {"decision": "promote_semantic"})

        assert prop is None, f"未知提案类型 {bogus!r} 不得生成提案（宁 miss 不脏写，不降级为 add）"

        with Session(proposer_env) as s:
            from lantai.models.tables import MemoryCandidate
            from lantai.models.tables import MemoryProposal as MP

            rows = s.exec(select(MP)).all()
            assert rows == [], f"未知类型不得落库任何提案，实得 {[r.id for r in rows]}"
            cand = s.get(MemoryCandidate, "cand_tg")
            assert cand.status == "gated", f"候选应转 gated，实得 {cand.status}"
