r"""沉潜（ADR-0036）：闲时夜梦沉淀与记忆折叠压缩测试。

验证：
1. find_consolidation_clusters 能够按 domain/lane 与主题聚类出 $\ge 3$ 条的碎片记忆集；
2. consolidate_cluster 按 CONSOLIDATION_AUDIT_MODE 三模式分流（ADR-0050）：
   off 直写零漂移 / shadow 影子留痕 / enforce 只产 pending 提案；
3. promoter.apply_proposal consolidation 分支：apply 后主记忆 active + 碎片折叠 +
   FTS/向量同步 + 逐条真实 id checkpoint 带 proposal_id；reject 不脏写；
4. prune_decayed_synapses 自动修剪极度衰减的边缘噪音（status="archived"）；
5. REST POST /evolution/consolidate 与 MCP memory_consolidate 工具。

测试改动说明（ADR-0050 / 票 07，票 :53 逐条改动理由）：
- 原 test_find_and_consolidate_cluster 拆分：
  (a) 聚类断言与 chat_json patch 点（"lantai.services.consolidation_service.chat_json"）
      **保留**——不改聚类算法与提纯段替身边界（票 :65/:57，TrustMem 真实执行）；
  (b) 原 :75-80「consolidate_cluster 后 master active + source_ids」与 :82-88「碎片
      立即 consolidated」断言：默认 off 零漂移路径下仍成立，改入 off 测试并增
      「零新行」断言（ADR-0050 决策 2：off 冒烟须断言零新行）；enforce 新语义下
      source_ids 信息移入提案 evidence_ids、折叠断言整体后移到 apply 之后
      （未裁决前碎片保持现状，票 :37/:49）；
  (c) 新增 enforce/shadow/裁决/去重冷却/门禁/验收统计冒烟（不 mock 内部逻辑，
      仅替 LLM 提纯段与外部向量存储，param_env 底座）。
- test_prune_decayed_synapses **原样保留**（修剪路径不在本票范围）。
- REST/MCP 报告形状断言 **原样保留**：既有键（last_run/consolidated_groups/
  pruned_count）不变，proposals_created/mode/skipped_* 为增量键（向后兼容）；
  两处 patch 的是 run_consolidation_cycle 本体与报告形状，非内部逻辑，合规。
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import select

from lantai.api.app import app
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal
from lantai.models.schemas import ProposalDecisionReq
from lantai.models.tables import (
    ConsolidationRun,
    MemoryCheckpoint,
    MemoryEdge,
    MemoryItem,
    MemoryProposal,
)
from lantai.services.consolidation_service import (
    consolidate_cluster,
    consolidation_audit_report,
    find_consolidation_clusters,
    prune_decayed_synapses,
    run_consolidation_cycle,
)

_LLM_PURIFIED = {
    "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
    "importance": 0.9,
    "confidence": 0.95,
}
_FRAG_IDS = ["mem_frag_01", "mem_frag_02", "mem_frag_03"]


def _seed_fragments(s):
    """插入 3 条同主题偏好碎片记忆（与原测试同构，保证 jieba 聚类稳定命中）。"""
    s.add_all(
        [
            MemoryItem(
                id="mem_frag_01",
                content="大哥今天早上泡了明前大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
            ),
            MemoryItem(
                id="mem_frag_02",
                content="大哥喜欢喝大佛龙井茶，水温要求85度",
                lane="preference",
                domain="user",
                decay_score=0.88,
                status="active",
            ),
            MemoryItem(
                id="mem_frag_03",
                content="大哥日常饮品偏好为浙江新昌大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.85,
                status="active",
            ),
        ]
    )
    s.commit()


def _cluster_of_three(s):
    clusters = find_consolidation_clusters(s, min_cluster_size=3)
    assert len(clusters) >= 1
    cluster_ids = {m.id for m in clusters[0]}
    for fid in _FRAG_IDS:
        assert fid in cluster_ids
    return clusters[0]


def _fts_content(s, memory_id):
    """按 id 参数绑定查询 FTS 行内容（无行返回 None）。"""
    row = s.connection().execute(
        text("SELECT content FROM memory_fts WHERE memory_id = :mid"), {"mid": memory_id}
    ).fetchone()
    return row[0] if row else None


class RecordingVS:
    """向量库记录替身（只替外部存储副作用，记录 add/delete 供索引同步断言）。"""

    def __init__(self):
        self.added: list[str] = []
        self.deleted: list[str] = []

    def add(self, ids=None, embeddings=None, metadatas=None, **kwargs):
        self.added.extend(ids or [])

    def delete(self, ids, **kwargs):
        self.deleted.extend(ids)


class TestConsolidationDB:
    """真实 SQLite 数据库不 mock 冒烟单测。"""

    def test_find_and_consolidate_cluster_off_zero_drift(self, param_env):
        """off（默认）零漂移：直写路径与现状逐字节一致，且零新行（ADR-0050 决策 2）。

        改动理由：原 :75-88 断言在 off 下仍成立，移入本测试并增补零新行断言——
        不写任何提案行/运行留痕行、checkpoint 不带 proposal_id。
        """
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)

            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                rep = run_consolidation_cycle(session=s)
            # 报告既有键不变 + 增量键可见（off：提案 0、留痕 0）
            assert rep["status"] == "success"
            assert rep["consolidated_groups"] == 1
            assert rep["new_memories"] == 1
            assert rep["pruned_count"] == 0
            assert rep["mode"] == "off"
            assert rep["proposals_created"] == 0
            assert rep["error"] == ""

            master = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).one()
            assert "大佛龙井" in master.content
            assert set(master.source_ids) == set(_FRAG_IDS)
            for fid in _FRAG_IDS:
                frag = s.get(MemoryItem, fid)
                assert frag.status == "consolidated"

            # 零新行：off 期不落提案、不落运行留痕
            assert s.exec(select(MemoryProposal)).all() == []
            assert s.exec(select(ConsolidationRun)).all() == []
            # 伪 id checkpoint 现状不变（proposal_id=None）
            cp = s.exec(select(MemoryCheckpoint)).one()
            assert cp.memory_id == "cluster_consolidation"
            assert cp.proposal_id is None
            assert cp.trigger == "consolidation"

    def test_enforce_generate_pending_proposal_then_apply(self, param_env, monkeypatch):
        """enforce 全链不 mock 冒烟：生成侧只产提案（不落主记忆/不折叠/零 checkpoint），
        裁决 apply 后主记忆+折叠+FTS/向量同步+逐条真实 id checkpoint（带 proposal_id）。

        改动理由：原「未裁决即 active/consolidated」断言按票 :37/:49 后移到 apply 之后；
        落锚：apply 不得出现 trigger="gate"/"evolve" checkpoint（add 捕获分支吞并回归锚，
        ADR-0050 决策 3 最高风险点）。
        """
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")
        vs = RecordingVS()
        import lantai.retrieval.hybrid as hybrid_module

        with session_factory() as s:
            _seed_fragments(s)
            from lantai.storage.fts import sync_fts

            for fid in _FRAG_IDS:  # 碎片先入 FTS（索引清理断言的前提）
                sync_fts(s, fid, s.get(MemoryItem, fid).content)
            s.commit()
            cluster = _cluster_of_three(s)

            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                prop = consolidate_cluster(cluster, session=s)
            assert isinstance(prop, MemoryProposal)
            assert prop.status == "pending"
            assert prop.proposal_type == "consolidation"
            assert prop.decided_by == "consolidation"
            assert set(prop.evidence_ids) == set(_FRAG_IDS)
            assert prop.confidence == 0.95  # 终审⑧：漏填则 supersedes 边/案牍徽标显 0.0
            assert prop.proposed_patch["content"] == _LLM_PURIFIED["consolidated_content"]
            assert prop.proposed_patch["decay_class"] == "semantic"
            assert prop.proposed_patch["source_ids"] == _FRAG_IDS
            assert prop.reason.startswith("TrustMem")
            assert prop.provenance["mode"] == "enforce"

            # 未裁决：无新 active 主记忆（active 面恰为三条碎片）、碎片原样、生成阶段零 checkpoint
            active_ids = {
                m.id for m in s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
            }
            assert active_ids == set(_FRAG_IDS)
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "active"
            assert s.exec(select(MemoryCheckpoint)).all() == []
            prop_id = prop.id

        # 裁决 apply（真实裁决入口 decide_proposal → apply_proposal；仅替 promoter embed 外部网络）
        from lantai.services.evolution_service import decide_proposal

        # 注意：vector store 替身必须用测试体内联 MonkeyPatch.context——param_env 已对
        # hybrid.get_vector_store 打过补丁，若再用 monkeypatch 夹具叠加同一属性，撕卸
        # 顺序会让 param_env 的 DummyVS lambda 泄漏给后续真实 chroma 测试（全量门禁
        # test_genglou_retrieval/test_staged_eval 实证）。内联上下文生命周期完整包住
        # 业务调用，不依赖夹具撕卸顺序。
        with (
            patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr(hybrid_module, "get_vector_store", lambda: vs)
            res = decide_proposal(prop_id, ProposalDecisionReq(approve=True, reason="人工确认"))
        assert res["ok"] is True
        assert res["folded"] == 3
        assert res["edge_only"] == 0
        assert res["evidence_missing"] == 0

        with session_factory() as s:
            prop = s.get(MemoryProposal, prop_id)
            assert prop.status == "applied"
            assert prop.applied_at is not None
            assert prop.decision_reason == "人工确认"

            masters = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
            assert len(masters) == 1
            master = masters[0]
            assert master.content == _LLM_PURIFIED["consolidated_content"]
            assert set(master.source_ids) == set(_FRAG_IDS)
            assert master.decay_class == "semantic"
            assert master.confidence == 0.95
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "consolidated"

            # FTS 同步：主记忆入索引、碎片清理（现行直写不清碎片，enforce apply 收紧口径）
            master_fts = _fts_content(s, master.id)
            assert master_fts is not None and "大佛龙井" in master_fts
            for fid in _FRAG_IDS:
                assert _fts_content(s, fid) is None
            # 向量同步：主记忆 add、碎片 delete
            assert vs.added == [master.id]
            assert sorted(vs.deleted) == sorted(_FRAG_IDS)

            # 逐条真实 id checkpoint（1 主 + 3 碎片，全带 proposal_id、trigger=consolidation）
            ckpts = s.exec(
                select(MemoryCheckpoint).where(MemoryCheckpoint.proposal_id == prop_id)
            ).all()
            assert len(ckpts) == 4
            master_ckpt = [c for c in ckpts if c.memory_id == master.id]
            assert len(master_ckpt) == 1
            assert master_ckpt[0].before == {}  # 新建实体 before={}（仿 add 分支先例）
            assert master_ckpt[0].after["content"] == _LLM_PURIFIED["consolidated_content"]
            frag_ckpts = [c for c in ckpts if c.memory_id in _FRAG_IDS]
            assert len(frag_ckpts) == 3
            for c in frag_ckpts:
                assert c.before["status"] == "active"  # before＝apply 时刻实际现状
                assert c.after["status"] == "consolidated"
            assert all(c.trigger == "consolidation" for c in ckpts)
            # 落锚：不得出现 add 捕获分支的 gate/evolve checkpoint
            all_triggers = {c.trigger for c in s.exec(select(MemoryCheckpoint)).all()}
            assert all_triggers == {"consolidation"}

            # supersedes 边（主记忆→碎片，方向同 merge 分支；confidence=提案 confidence）
            edges = s.exec(select(MemoryEdge).where(MemoryEdge.relation == "supersedes")).all()
            assert len(edges) == 3
            assert {e.target_memory_id for e in edges} == set(_FRAG_IDS)
            assert all(e.source_memory_id == master.id for e in edges)
            assert all(e.confidence == 0.95 for e in edges)

    def test_enforce_reject_keeps_fragments_active(self, param_env, monkeypatch):
        """拒绝路径不脏写：碎片三条原样 active、无新增主记忆、无 checkpoint，
        提案终态非 pending 且 decision_reason 落库（票 :50-51）。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")

        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                prop = consolidate_cluster(cluster, session=s)
            prop_id = prop.id

        from lantai.services.evolution_service import decide_proposal

        res = decide_proposal(prop_id, ProposalDecisionReq(approve=False, reason="提纯丢失关键细节"))
        assert res["ok"] is True

        with session_factory() as s:
            active_ids = {
                m.id for m in s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
            }
            assert active_ids == set(_FRAG_IDS)
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "active"
            prop = s.get(MemoryProposal, prop_id)
            assert prop.status == "rejected"
            assert prop.decision_reason == "提纯丢失关键细节"
            assert s.exec(select(MemoryCheckpoint)).all() == []
            assert s.exec(select(MemoryEdge)).all() == []

    def test_enforce_apply_evidence_triage(self, param_env, monkeypatch):
        """apply 对 evidence 三分（ADR-0050 决策 3）：仍 active→折叠+边+checkpoint；
        已 archived→仅补边（血缘补记无 checkpoint）；已删除→不建边留日志。
        pending 期碎片可被遗忘/修剪/笔削变更——三分处置兜底。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")

        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                prop = consolidate_cluster(cluster, session=s)
            prop_id = prop.id

            # pending 期变更：frag_02 被遗忘归档、frag_03 被笔削删除、frag_01 保持 active
            s.get(MemoryItem, "mem_frag_02").status = "archived"
            s.delete(s.get(MemoryItem, "mem_frag_03"))
            s.commit()

        # 向量库用 param_env 的 DummyVS（本用例不断言向量副作用，不叠加补丁防泄漏）
        with patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]):
            res = apply_proposal(prop_id)
        assert res["ok"] is True
        assert res["folded"] == 1
        assert res["edge_only"] == 1
        assert res["evidence_missing"] == 1

        with session_factory() as s:
            assert s.get(MemoryItem, "mem_frag_01").status == "consolidated"
            assert s.get(MemoryItem, "mem_frag_02").status == "archived"  # 不改状态
            assert s.get(MemoryItem, "mem_frag_03") is None  # 幽灵碎片不建边
            edges = s.exec(select(MemoryEdge).where(MemoryEdge.relation == "supersedes")).all()
            assert {e.target_memory_id for e in edges} == {"mem_frag_01", "mem_frag_02"}
            ckpts = s.exec(
                select(MemoryCheckpoint).where(MemoryCheckpoint.proposal_id == prop_id)
            ).all()
            # 仅主记忆 + frag_01 两笔（archived 补边无 checkpoint、已删除无行）
            assert len(ckpts) == 2
            assert {c.memory_id for c in ckpts} & set(_FRAG_IDS) == {"mem_frag_01"}

    def test_enforce_apply_stale_gate(self, param_env, monkeypatch):
        """stale 硬门：任一 evidence 已 consolidated 即拒绝 apply（模式回切/重叠提案防护，
        ADR-0050 决策 3）——明确 reason 返回，不产生双主记忆。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")

        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                first = consolidate_cluster(cluster, session=s)
            # 模拟重叠集并行提案：同证据第二条 pending 提案
            s.add(
                MemoryProposal(
                    id="prop_second",
                    proposal_type="consolidation",
                    evidence_ids=list(_FRAG_IDS),
                    reason="dup",
                    proposed_patch=dict(first.proposed_patch),
                    confidence=0.9,
                    status="pending",
                    decided_by="consolidation",
                )
            )
            s.commit()
            first_id = first.id

        # 先行 apply 的重叠提案：第一条正常应用（碎片折叠）
        # 向量库用 param_env 的 DummyVS（本用例不断言向量副作用，不叠加补丁防泄漏）
        with patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]):
            assert apply_proposal(first_id)["ok"] is True
            # 后至者被 stale 硬门拒绝（evidence 已 consolidated）
            res = apply_proposal("prop_second")
        assert res["ok"] is False
        assert res["reason"] == "stale: evidence already consolidated"

        with session_factory() as s:
            # 恰一笔主记忆（双主记忆被硬门封死）
            assert len(s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()) == 1
            # stale 提案落终态 rejected 并留痕（不得停在 approved：evolve_worker 专捞
            # APPROVED 重试，apply 每次早退不改状态将致 livelock——本用例固化该修复）
            stale = s.get(MemoryProposal, "prop_second")
            assert stale.status == "rejected"
            assert "stale" in (stale.decision_reason or "").lower()

    def test_stale_gate_via_decide_proposal_no_livelock(self, param_env, monkeypatch):
        """回归（实证修复）：走文档化裁决入口 decide_proposal 批准一条 stale 提案——
        decide 先置 APPROVED 再调 apply，apply 若早退不改状态，提案将永停 approved，
        evolve_worker.run_pending_proposals（专捞 APPROVED）每轮重试每次失败＝livelock。
        本用例断言：apply 被 stale 门拒后提案落终态 rejected，worker 不再捞到它。"""
        from lantai.services.evolution_service import decide_proposal
        from lantai.workers.evolve_worker import run_pending_proposals

        session_factory, _ = param_env
        with session_factory() as s:
            # 两条已 consolidated 的 evidence（模拟先行 apply 之后）＋一条 stale 提案
            s.add(
                MemoryItem(
                    id="stale_f1",
                    content="大佛龙井 龙井 茶 杭州",
                    lane="general",
                    status="consolidated",
                    source_ids=["x"],
                )
            )
            s.add(
                MemoryItem(
                    id="stale_f2",
                    content="大佛龙井 龙井 茶 杭州",
                    lane="general",
                    status="consolidated",
                    source_ids=["y"],
                )
            )
            s.add(
                MemoryProposal(
                    id="prop_stale_decide",
                    proposal_type="consolidation",
                    evidence_ids=["stale_f1", "stale_f2"],
                    reason="dup",
                    proposed_patch={
                        "content": "提纯主记忆",
                        "domain": "user",
                        "lane": "general",
                        "confidence": 0.9,
                        "importance": 0.8,
                    },
                    confidence=0.9,
                    status="pending",
                    decided_by="consolidation",
                )
            )
            s.commit()

        with patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]):
            res = decide_proposal(
                "prop_stale_decide", ProposalDecisionReq(approve=True, reason="批准")
            )
        assert res["ok"] is False
        assert res["reason"] == "stale: evidence already consolidated"

        with session_factory() as s:
            prop = s.get(MemoryProposal, "prop_stale_decide")
            # 关键断言：不得停在 approved（否则 evolve_worker 无限重试）
            assert prop.status == "rejected"
            assert "stale" in (prop.decision_reason or "").lower()

        # evolve_worker 常态巡检不再捞到它（零重试）
        with patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]):
            run_pending_proposals()
        with session_factory() as s:
            assert s.get(MemoryProposal, "prop_stale_decide").status == "rejected"

    def test_shadow_direct_write_plus_shadow_proposal(self, param_env, monkeypatch):
        """shadow：直写+折叠照旧，另落恰一条 SHADOW 影子提案；伪 id checkpoint 填影子提案 id；
        影子行结构性不可裁决/不可应用（双门禁天然拦截），不进 pending 裁决队列。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "shadow")

        with session_factory() as s:
            _seed_fragments(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                rep = run_consolidation_cycle(session=s)
            assert rep["status"] == "success"
            assert rep["new_memories"] == 1
            assert rep["proposals_created"] == 1
            master = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).one()
            assert "大佛龙井" in master.content
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "consolidated"

            props = s.exec(select(MemoryProposal)).all()
            assert len(props) == 1
            shadow = props[0]
            assert shadow.status == "shadow"
            assert shadow.decided_by == "shadow"
            assert set(shadow.evidence_ids) == set(_FRAG_IDS)
            assert shadow.confidence == 0.95
            assert shadow.provenance["master_id"] == master.id  # 对账键
            assert shadow.proposed_patch["content"] == master.content

            cp = s.exec(select(MemoryCheckpoint)).one()
            assert cp.memory_id == "cluster_consolidation"
            assert cp.proposal_id == shadow.id  # 生成留痕↔产物对账

            # 运行留痕一行（mode=shadow，purified_ok=直写数）
            runs = s.exec(select(ConsolidationRun)).all()
            assert len(runs) == 1
            assert runs[0].mode == "shadow"
            assert runs[0].purified_ok == 1
            assert runs[0].proposals_created == 1
            shadow_id = shadow.id

        # 双门禁：影子行不可裁决（decide_proposal 仅受理 PENDING）也不可应用
        from lantai.services.evolution_service import decide_proposal, list_proposals

        assert list_proposals(status="pending")["proposals"] == []
        with pytest.raises(RuntimeError):
            decide_proposal(shadow_id, ProposalDecisionReq(approve=True, reason="影子行不可裁决"))

    def test_aggregate_master_threshold_is_configurable(self, param_env, monkeypatch):
        """聚合主记忆判定阈值可配（ADR-0002 零硬编码）：原硬编码 len(source_ids)>=3，
        现取 settings.CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES——调高后同一条
        多源记忆不再被判为「已是聚合主记忆」而跳过，即重新进入聚类候选面。"""
        session_factory, _ = param_env
        with session_factory() as s:
            # 一条 source_ids 恰 3 的多源记忆：默认阈值下应被跳过（不参与聚类）
            s.add(
                MemoryItem(
                    id="mem_agg",
                    content="聚合主记忆 大佛龙井 龙井 茶 杭州",
                    lane="general",
                    domain="user",
                    status="active",
                    source_ids=["a", "b", "c"],
                )
            )
            s.commit()

            monkeypatch.setattr(
                settings, "CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES", 3
            )
            assert all(
                "mem_agg" not in [m.id for m in c] for c in find_consolidation_clusters(s)
            )

            # 阈值调高到 4 → 同一条不再被判为聚合主记忆，重新进入候选面
            monkeypatch.setattr(
                settings, "CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES", 4
            )
            clusters = find_consolidation_clusters(s)
            # 单条不足以成簇（<min_cluster_size），但已被纳入分组＝不再是「跳过」态；
            # 用第二条同关键词碎片凑够簇验证它确实回到了候选面
            s.add(
                MemoryItem(
                    id="mem_f2",
                    content="大佛龙井 龙井 茶 杭州 另一条",
                    lane="general",
                    domain="user",
                    status="active",
                )
            )
            s.add(
                MemoryItem(
                    id="mem_f3",
                    content="大佛龙井 龙井 茶 杭州 第三条",
                    lane="general",
                    domain="user",
                    status="active",
                )
            )
            s.commit()
            ids = {m.id for c in find_consolidation_clusters(s) for m in c}
            assert "mem_agg" in ids  # 阈值放开后回到候选面

    def test_enforce_dedup_and_cooldown(self, param_env, monkeypatch):
        """生成侧幂等去重与拒绝冷却（ADR-0050 决策 3）：同 evidence pending 已存在→跳过；
        冷却期内 rejected→跳过；冷却期满→允许再奏。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")

        with session_factory() as s:
            _seed_fragments(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                rep1 = run_consolidation_cycle(session=s)
                assert rep1["proposals_created"] == 1
                assert rep1["skipped_dupes"] == 0
                assert rep1["status"] == "success"  # 只产提案不再误报 idle

                # 第二夜：同簇再次聚出 → 幂等去重（每夜重复生成被封）
                rep2 = run_consolidation_cycle(session=s)
                assert rep2["proposals_created"] == 0
                assert rep2["skipped_dupes"] == 1
                assert len(s.exec(select(MemoryProposal)).all()) == 1

                prop = s.exec(select(MemoryProposal)).one()
                # 用户拒绝 → 冷却期内不再重复奏（拒一次 ≠ 订阅每日打扰）
                prop.status = "rejected"
                prop.decision_reason = "人工拒绝"
                s.add(prop)
                s.commit()
                rep3 = run_consolidation_cycle(session=s)
                assert rep3["skipped_rejected_cooldown"] == 1
                assert rep3["proposals_created"] == 0

                # 冷却期满（30 天前拒绝）→ 允许再奏（拒的是「当时产物」非永久禁令）
                prop.created_at = utcnow() - timedelta(
                    days=settings.CONSOLIDATION_REJECTED_COOLDOWN_DAYS + 1
                )
                s.add(prop)
                s.commit()
                rep4 = run_consolidation_cycle(session=s)
                assert rep4["proposals_created"] == 1
                assert rep4["skipped_rejected_cooldown"] == 0
                assert len(s.exec(select(MemoryProposal)).all()) == 2

    def test_report_key_contract_matches_initial(self, param_env):
        """报告键集契约：初值键集须等于 ADR-0050 决策 3 列明的报告契约键集。

        改动理由：本票新增 mode/proposals_created/skipped_*/error 后，模块级
        _LAST_CONSOLIDATION_REPORT 初值仍只有 5 个旧键，而 REST
        /evolution/consolidate/report 与 MCP consolidation_report 直接透传该 dict——
        首次巩固运行前读取新键即 KeyError。真值取自 ADR 契约（独立于被测代码的字面清单）。
        """
        import lantai.services.consolidation_service as cs

        # ADR-0050 决策 3 报告契约：既有键不变 + 增量键（mode/proposals_created/
        # skipped_* /error）。off 期 new_memories/consolidated_groups/pruned_count 语义不变。
        contract = {
            "last_run",
            "consolidated_groups",
            "new_memories",
            "pruned_count",
            "status",
            "mode",
            "proposals_created",
            "skipped_dupes",
            "skipped_rejected_cooldown",
            "skipped_lowq",
            "error",
        }
        assert set(cs._LAST_CONSOLIDATION_REPORT) == contract

        # 运行后报告亦须同键集（初值与运行时契约不漂移）
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                ran = run_consolidation_cycle(session=s)
        assert set(ran) == contract

    def test_illegal_mode_fail_loud(self, param_env, monkeypatch):
        """非法值 fail-loud（ADR-0050 决策 1）：尾随空格等非法值拒绝执行本周期巩固，
        ERROR 留痕 + report 可见——不静默回落 off（off 恰是要消灭的脏写面）。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "off ")  # 尾随空格

        with session_factory() as s:
            _seed_fragments(s)
            rep = run_consolidation_cycle(session=s)
            assert rep["status"] == "refused"
            assert "非法值" in rep["error"]
            assert rep["mode"] == "off "
            assert rep["proposals_created"] == 0 and rep["new_memories"] == 0
            # 未执行巩固：无提案、碎片原样
            assert s.exec(select(MemoryProposal)).all() == []
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "active"
            # 拒绝事件持久留痕（与 shadow 硬时限拒绝同轨，ADR-0050 决策 9）：
            # 仅内存 report + logger 无持久痕，配置错误排查将无所依凭。
            refused_runs = s.exec(select(ConsolidationRun)).all()
            assert len(refused_runs) == 1
            assert "非法值" in refused_runs[0].error
            # 留痕记原非法值（非回落字面量），否则事后无从知道配错成了什么
            assert refused_runs[0].mode == "off "

    def test_shadow_deadline_hard_limit(self, param_env, monkeypatch):
        """shadow 硬时限（ADR-0050 决策 4）：自首条 mode=shadow 留痕起算，超期拒绝执行
        巩固并 ERROR 留痕——静默直写不能在过审制名义下无限合法存续。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "shadow")

        with session_factory() as s:
            _seed_fragments(s)
            s.add(
                ConsolidationRun(
                    id="run_old",
                    ran_at=utcnow() - timedelta(days=settings.CONSOLIDATION_SHADOW_MAX_DAYS + 1),
                    mode="shadow",
                )
            )
            s.commit()

            rep = run_consolidation_cycle(session=s)
            assert rep["status"] == "refused"
            assert "硬时限" in rep["error"]
            # 未执行巩固：无影子提案、碎片原样；拒绝事件本身留痕（error 非空行）
            assert s.exec(select(MemoryProposal)).all() == []
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "active"
            refused_runs = s.exec(select(ConsolidationRun)).all()
            assert any(r.error for r in refused_runs)

    def test_audit_report_enforce(self, param_env, monkeypatch):
        """验收统计出口（ADR-0050 决策 9）：enforce 自证①②合取——比例恰 100% +
        伪 id 直写指纹为 0；无样本比例 None 不编造。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")

        # 无样本：比例 None（不编造）
        empty = consolidation_audit_report(window_days=7)
        assert empty["enforce"]["proposal_ratio"] is None
        assert empty["enforce"]["ratio_ok"] is None
        # 合取口径无样本时亦返回 None（不编造「通过」）
        assert empty["enforce"]["self_attestation_ok"] is None

        with session_factory() as s:
            _seed_fragments(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                rep = run_consolidation_cycle(session=s)
            assert rep["proposals_created"] == 1
            prop_id = s.exec(select(MemoryProposal)).one().id

        report = consolidation_audit_report(window_days=7)
        assert report["enforce"]["purified_ok"] == 1
        assert report["enforce"]["proposals_created"] == 1
        assert report["enforce"]["proposal_ratio"] == 1.0
        assert report["enforce"]["ratio_ok"] is True
        assert report["enforce"]["pseudo_id_checkpoints"] == 0
        assert report["enforce"]["direct_write_fingerprint_ok"] is True
        # ①②合取（ADR-0050 决策 9）：仅①则同路径写两表的 bug 不可见，
        # 仅②则绕过两路的直写不可见——合取方是票面「100% 可证伪」的完整断言。
        assert report["enforce"]["self_attestation_ok"] is True
        assert report["proposal_buckets"]["pending"] == 1

        # 裁决后提案转入 applied，仍计入窗口创建口径（created_at∈窗口）
        with patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]):
            assert apply_proposal(prop_id)["ok"] is True
        report2 = consolidation_audit_report(window_days=7)
        assert report2["proposal_buckets"]["applied"] == 1
        assert report2["enforce"]["proposals_created"] == 1
        assert report2["enforce"]["ratio_ok"] is True
        assert report2["enforce"]["self_attestation_ok"] is True

        # 直写指纹违约（off 式直写残留）→ 合取必须转 False：
        # 仅①比例仍 100%，唯②捕获直写——单看 ratio_ok 会误判「通过」。
        with session_factory() as s:
            s.add(
                MemoryCheckpoint(
                    id="ckpt_direct_write",
                    memory_id="cluster_consolidation",
                    version=1,
                    before={},
                    after={},
                    proposal_id=None,
                    trigger="consolidation",
                )
            )
            s.commit()
        report3 = consolidation_audit_report(window_days=7)
        assert report3["enforce"]["ratio_ok"] is True  # ① 单独看仍 OK
        assert report3["enforce"]["direct_write_fingerprint_ok"] is False  # ② 捕获
        assert report3["enforce"]["self_attestation_ok"] is False  # 合取转 False

    def test_audit_report_shadow_three_way(self, param_env, monkeypatch):
        """shadow 三方互证（ADR-0050 决策 9）：伪 id checkpoint 行数 = 影子提案数 =
        mode=shadow purified_ok。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "shadow")

        with session_factory() as s:
            _seed_fragments(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                run_consolidation_cycle(session=s)

        report = consolidation_audit_report(window_days=7)
        assert report["shadow"]["shadow_proposals"] == 1
        assert report["shadow"]["purified_ok"] == 1
        assert report["shadow"]["pseudo_id_checkpoints"] == 1
        assert report["shadow"]["three_way_ok"] is True

    def test_audit_cli_smoke(self, param_env, monkeypatch, capsys):
        """scripts/consolidation_audit_report.py 出口冒烟（库经 param_env 注入）。"""
        import scripts.consolidation_audit_report as cli

        monkeypatch.setattr("sys.argv", ["consolidation_audit_report.py", "--days", "7"])
        assert cli.main() == 0
        assert "enforce" in capsys.readouterr().out

    def test_prune_decayed_synapses(self, param_env):
        """原样保留：修剪路径不在本票范围（票 :65）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            # 插入 1 条极低衰减且无用的记忆
            m_decayed = MemoryItem(
                id="mem_decayed_01",
                content="临时去了一趟超市买餐巾纸",
                lane="general",
                decay_score=0.02,
                helpful_count=0,
                status="active",
            )
            m_healthy = MemoryItem(
                id="mem_healthy_01",
                content="华硕天选三搭载 RTX 3050 显卡",
                lane="fact",
                decay_score=0.9,
                helpful_count=5,
                status="active",
            )
            s.add_all([m_decayed, m_healthy])
            s.commit()

            pruned = prune_decayed_synapses(threshold=0.05, session=s)
            assert pruned == 1

            s.refresh(m_decayed)
            s.refresh(m_healthy)
            assert m_decayed.status == "archived"
            assert m_healthy.status == "active"


class TestConsolidationEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具（报告形状断言原样保留：既有键不变，新键为增量）。"""

    def test_rest_evolution_consolidate(self, param_env):
        client = TestClient(app)
        with patch(
            "lantai.services.consolidation_service.run_consolidation_cycle",
            return_value={
                "consolidated_groups": 1,
                "new_memories": 1,
                "pruned_count": 2,
                "status": "success",
            },
        ):
            resp = client.post("/evolution/consolidate")
            assert resp.status_code == 200
            data = resp.json()
            assert data["consolidated_groups"] == 1
            assert data["pruned_count"] == 2

    def test_mcp_consolidation_tools(self, param_env):
        from lantai.cli.mcp import handle_consolidation_report, handle_memory_consolidate

        with patch(
            "lantai.services.consolidation_service.run_consolidation_cycle",
            return_value={
                "consolidated_groups": 0,
                "new_memories": 0,
                "pruned_count": 0,
                "status": "idle",
            },
        ):
            res = handle_memory_consolidate({})
            assert res["status"] == "idle"

        rep = handle_consolidation_report({})
        assert "last_run" in rep or "status" in rep
