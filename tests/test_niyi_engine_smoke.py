"""拟议（Niyi）核心演化审议与因果变化推演引擎冒烟测试 (Ticket 01)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测核心引擎，不 mock 核心计算、存储与因果拓扑逻辑；
- 覆盖 4 个公共接缝：
  1. 提案变更因果影响推演与 DeliberationOutcome 结构化提取；
  2. 未决提案互斥与重叠审计（audit_pending_proposals）；
  3. 樊篱 wrap_as_data 隔离标记；
  4. 租户与属主行级 ACL 隔离（跨租户记忆与提案禁止混合推演）。
"""

from __future__ import annotations

import pytest

from lantai.cognition.niyi import DeliberationOutcome, NiyiEngine
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.storage import db


def _seed_memory_with_edges(
    user_id: str = "user-niyi-1",
    tenant_id: str | None = "tenant-a",
) -> tuple[MemoryItem, list[MemoryEdge]]:
    t = utcnow()
    with db.get_session() as s:
        mem = MemoryItem(
            id=f"mem-niyi-target-{t.timestamp()}",
            content="生产环境网关严格限制每个 IP 每分钟最多 60 次突发请求",
            confidence=0.9,
            domain="user",
            lane="rule",
            status="active",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
            updated_at=t,
        )
        s.add(mem)

        # 构造下游依赖边
        edge1 = MemoryEdge(
            id=f"edge-niyi-1-{t.timestamp()}",
            source_memory_id=mem.id,
            target_memory_id=f"mem-downstream-1-{t.timestamp()}",
            relation="causes",
            confidence=0.85,
            reason="限流规则直接导致高频爬虫请求被阻断并返回 429",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
        )
        edge2 = MemoryEdge(
            id=f"edge-niyi-2-{t.timestamp()}",
            source_memory_id=mem.id,
            target_memory_id=f"mem-downstream-2-{t.timestamp()}",
            relation="supports",
            confidence=0.9,
            reason="支撑网关安全防御策略核心指标",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
        )
        s.add(edge1)
        s.add(edge2)
        s.commit()
        s.refresh(mem)
        s.refresh(edge1)
        s.refresh(edge2)
        return mem, [edge1, edge2]


def _seed_pending_proposals(
    target_id: str,
    user_id: str = "user-niyi-1",
    tenant_id: str | None = "tenant-a",
) -> list[MemoryProposal]:
    t = utcnow()
    with db.get_session() as s:
        p1 = MemoryProposal(
            id=f"prop-niyi-1-{t.timestamp()}",
            proposal_type="update",
            target_memory_id=target_id,
            reason="将限流由 60 次上调至 100 次以适应高峰流量",
            proposed_patch={"confidence": 0.95, "content": "网关限流阈值上调为 100 次/分"},
            confidence=0.88,
            status="pending",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
        )
        p2 = MemoryProposal(
            id=f"prop-niyi-2-{t.timestamp()}",
            proposal_type="deprecate",
            target_memory_id=target_id,
            reason="废弃旧版网关限流规则，改由云原生防护组件接管",
            proposed_patch={"status": "deprecated"},
            confidence=0.75,
            status="pending",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
        )
        s.add(p1)
        s.add(p2)
        s.commit()
        s.refresh(p1)
        s.refresh(p2)
        return [p1, p2]


class TestNiyiEngineSmoke:
    """Niyi 核心引擎 4 大接缝冒烟测试集。"""

    def test_seam1_deliberate_proposal_impact(self):
        """Seam 1: 提案变更因果影响推演与 DeliberationOutcome 结构化提取。"""
        mem, edges = _seed_memory_with_edges(user_id="user-niyi-seam1")
        p = Principal(user_id="user-niyi-seam1", tenant_id="tenant-a")
        engine = NiyiEngine(principal=p)

        # 1. 模拟废弃操作（存在下游拓扑边时，应当评定为 high 风险）
        outcome_dep = engine.deliberate_proposal(
            proposal_type="deprecate",
            patch_data={"status": "deprecated"},
            target_memory_id=mem.id,
        )
        assert outcome_dep["target_memory_id"] == mem.id
        assert outcome_dep["proposal_type"] == "deprecate"
        assert outcome_dep["impacted_edges_count"] == 2
        assert outcome_dep["risk_level"] == "high"
        assert (
            "断裂" in outcome_dep["deliberation_reason"]
            or "悬空" in outcome_dep["deliberation_reason"]
            or "因果" in outcome_dep["deliberation_reason"]
        )

        # 2. 模拟新事实增加（无下游依赖，应当评定为 low 风险）
        outcome_add = engine.deliberate_proposal(
            proposal_type="add",
            patch_data={"content": "新增 Redis 哨兵集群监控规则"},
            target_memory_id=None,
        )
        assert outcome_add["proposal_type"] == "add"
        assert outcome_add["impacted_edges_count"] == 0
        assert outcome_add["risk_level"] == "low"

    def test_seam2_audit_pending_proposals(self):
        """Seam 2: 未决提案冲突与重叠审计（audit_pending_proposals）。"""
        mem, _ = _seed_memory_with_edges(user_id="user-niyi-seam2")
        props = _seed_pending_proposals(target_id=mem.id, user_id="user-niyi-seam2")
        p = Principal(user_id="user-niyi-seam2", tenant_id="tenant-a")
        engine = NiyiEngine(principal=p)

        audit_res = engine.audit_pending_proposals(limit=10)
        assert audit_res["total_pending"] >= 2
        assert len(audit_res["conflicting_pairs"]) >= 1

        # 验证识别到了针对 mem.id 的竞争提案对
        conflict = audit_res["conflicting_pairs"][0]
        assert conflict["target_memory_id"] == mem.id
        assert {conflict["proposal_id_a"], conflict["proposal_id_b"]} == {props[0].id, props[1].id}
        assert "竞争" in conflict["description"] or "冲突" in conflict["description"]

    def test_seam3_fencing_protection(self):
        """Seam 3: 樊篱 wrap_as_data 隔离标记防护。"""
        mem, _ = _seed_memory_with_edges(user_id="user-niyi-seam3")
        p = Principal(user_id="user-niyi-seam3", tenant_id="tenant-a")
        engine = NiyiEngine(principal=p)

        outcome = engine.deliberate_proposal(
            proposal_type="update",
            patch_data={"content": "更新网关白名单配置"},
            target_memory_id=mem.id,
        )
        # 樊篱防护检查
        assert "fenced_reason" in outcome
        assert '<memory_data id="' in outcome["fenced_reason"]
        assert "</memory_data>" in outcome["fenced_reason"]

        # 审计摘要樊篱检查
        _seed_pending_proposals(target_id=mem.id, user_id="user-niyi-seam3")
        audit_res = engine.audit_pending_proposals(limit=10)
        assert "fenced_summary" in audit_res
        assert '<memory_data id="' in audit_res["fenced_summary"]
        assert "</memory_data>" in audit_res["fenced_summary"]

    def test_seam4_row_level_acl_isolation(self):
        """Seam 4: 租户与属主行级 ACL 隔离（跨租户记忆与提案禁止混合推演）。"""
        mem_a, _ = _seed_memory_with_edges(user_id="tenant-a-user", tenant_id="tenant-a")
        _ = _seed_pending_proposals(
            target_id=mem_a.id, user_id="tenant-a-user", tenant_id="tenant-a"
        )

        # 租户 B 的用户介入
        p_b = Principal(user_id="tenant-b-user", tenant_id="tenant-b")
        engine_b = NiyiEngine(principal=p_b)

        # 1. 尝试推演租户 A 的记忆 -> 无法访问，返回安全降级（未找到目标或无权访问，risk_level="low", impacted_edges=0）
        outcome = engine_b.deliberate_proposal(
            proposal_type="deprecate",
            patch_data={"status": "deprecated"},
            target_memory_id=mem_a.id,
        )
        assert outcome["impacted_edges_count"] == 0
        assert "未找到目标记忆或无权访问" in outcome["deliberation_reason"]

        # 2. 租户 B 审计未决提案 -> 不应包含租户 A 的提案对
        audit_res_b = engine_b.audit_pending_proposals(limit=20)
        assert not any(c["target_memory_id"] == mem_a.id for c in audit_res_b["conflicting_pairs"])
