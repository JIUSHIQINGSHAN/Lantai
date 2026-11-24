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
        child1 = MemoryItem(
            id=edge1.target_memory_id,
            content="下游限流结果",
            confidence=0.8,
            domain="user",
            lane="rule",
            status="active",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
            updated_at=t,
        )
        child2 = MemoryItem(
            id=edge2.target_memory_id,
            content="下游安全指标",
            confidence=0.8,
            domain="user",
            lane="rule",
            status="active",
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=t,
            updated_at=t,
        )
        s.add(child1)
        s.add(child2)
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
        assert set(outcome_dep["topology_impact"]["downstream_node_ids"]) == {
            edges[0].target_memory_id,
            edges[1].target_memory_id,
        }
        assert set(outcome_dep["topology_impact"]["disconnected_node_ids"]) == {
            edges[0].target_memory_id,
            edges[1].target_memory_id,
        }
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

    def test_seam5_topology_keeps_descendants_with_an_alternate_causal_path(self):
        mem, edges = _seed_memory_with_edges(user_id="user-niyi-alternate-path")
        t = utcnow()
        child_id = edges[0].target_memory_id
        grandchild_id = f"mem-niyi-grandchild-{t.timestamp()}"
        alternate_source_id = f"mem-niyi-alternate-source-{t.timestamp()}"
        with db.get_session() as s:
            s.add(
                MemoryItem(
                    id=grandchild_id,
                    content="间接依赖结果",
                    confidence=0.8,
                    domain="user",
                    lane="rule",
                    status="active",
                    user_id="user-niyi-alternate-path",
                    tenant_id="tenant-a",
                    created_at=t,
                    updated_at=t,
                )
            )
            s.add(
                MemoryItem(
                    id=alternate_source_id,
                    content="替代因果来源",
                    confidence=0.8,
                    domain="user",
                    lane="rule",
                    status="active",
                    user_id="user-niyi-alternate-path",
                    tenant_id="tenant-a",
                    created_at=t,
                    updated_at=t,
                )
            )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-grandchild-{t.timestamp()}",
                    source_memory_id=child_id,
                    target_memory_id=grandchild_id,
                    relation="causes",
                    confidence=0.9,
                    reason="间接影响",
                    user_id="user-niyi-alternate-path",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-alternate-{t.timestamp()}",
                    source_memory_id=alternate_source_id,
                    target_memory_id=child_id,
                    relation="causes",
                    confidence=0.9,
                    reason="替代支撑路径",
                    user_id="user-niyi-alternate-path",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id="user-niyi-alternate-path", tenant_id="tenant-a")
        ).deliberate_proposal("deprecate", {"status": "deprecated"}, mem.id)

        topology = outcome["topology_impact"]
        assert set(topology["downstream_node_ids"]) == {
            edges[0].target_memory_id,
            edges[1].target_memory_id,
            grandchild_id,
        }
        assert child_id not in topology["disconnected_node_ids"]
        assert grandchild_id not in topology["disconnected_node_ids"]

    def test_seam5b_triggered_by_edges_follow_causal_direction(self):
        t = utcnow()
        root_id = f"mem-niyi-trigger-root-{t.timestamp()}"
        mid_id = f"mem-niyi-trigger-mid-{t.timestamp()}"
        leaf_id = f"mem-niyi-trigger-leaf-{t.timestamp()}"
        user_id = "user-niyi-triggered-by"
        with db.get_session() as s:
            for memory_id, content in (
                (root_id, "根因"),
                (mid_id, "中间故障"),
                (leaf_id, "最终影响"),
            ):
                s.add(
                    MemoryItem(
                        id=memory_id,
                        content=content,
                        confidence=0.8,
                        domain="user",
                        lane="rule",
                        status="active",
                        user_id=user_id,
                        tenant_id="tenant-a",
                        created_at=t,
                        updated_at=t,
                    )
                )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-trigger-mid-{t.timestamp()}",
                    source_memory_id=mid_id,
                    target_memory_id=root_id,
                    relation="triggered_by",
                    confidence=0.9,
                    reason="中间故障由根因触发",
                    user_id=user_id,
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-trigger-leaf-{t.timestamp()}",
                    source_memory_id=leaf_id,
                    target_memory_id=mid_id,
                    relation="triggered_by",
                    confidence=0.85,
                    reason="最终影响由中间故障触发",
                    user_id=user_id,
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id=user_id, tenant_id="tenant-a")
        ).deliberate_proposal("deprecate", {"status": "deprecated"}, root_id)

        topology = outcome["topology_impact"]
        assert topology["direct_causal_edges_count"] == 1
        assert topology["downstream_node_ids"] == sorted([mid_id, leaf_id])
        assert topology["disconnected_node_ids"] == sorted([mid_id, leaf_id])

    def test_seam6_disjoint_updates_to_one_target_are_not_mutex(self):
        mem, _ = _seed_memory_with_edges(user_id="user-niyi-compatible-proposals")
        t = utcnow()
        with db.get_session() as s:
            s.add(
                MemoryProposal(
                    id=f"prop-niyi-content-{t.timestamp()}",
                    proposal_type="update",
                    target_memory_id=mem.id,
                    reason="修正文案",
                    proposed_patch={"content": "修正后的事实"},
                    confidence=0.8,
                    status="pending",
                    user_id="user-niyi-compatible-proposals",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.add(
                MemoryProposal(
                    id=f"prop-niyi-lane-{t.timestamp()}",
                    proposal_type="update",
                    target_memory_id=mem.id,
                    reason="调整分轨",
                    proposed_patch={"lane": "experience"},
                    confidence=0.8,
                    status="pending",
                    user_id="user-niyi-compatible-proposals",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        audit = NiyiEngine(
            principal=Principal(user_id="user-niyi-compatible-proposals", tenant_id="tenant-a")
        ).audit_pending_proposals(limit=10)

        assert audit["total_pending"] == 2
        assert audit["conflicting_pairs"] == []

    def test_seam7_different_targets_are_not_compared_by_text_similarity(self):
        _seed_memory_with_edges(user_id="user-niyi-topic-conflict")
        t = utcnow()
        with db.get_session() as s:
            s.add(
                MemoryProposal(
                    id=f"prop-niyi-topic-update-{t.timestamp()}",
                    proposal_type="update",
                    target_memory_id="mem-niyi-topic-a",
                    reason="生产网关限流阈值提升到每分钟一百次请求",
                    proposed_patch={"content": "生产网关限流阈值提升到每分钟一百次请求"},
                    confidence=0.8,
                    status="pending",
                    user_id="user-niyi-topic-conflict",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.add(
                MemoryProposal(
                    id=f"prop-niyi-topic-deprecate-{t.timestamp()}",
                    proposal_type="deprecate",
                    target_memory_id="mem-niyi-topic-b",
                    reason="生产网关限流阈值保持每分钟六十次请求",
                    proposed_patch={"status": "deprecated"},
                    confidence=0.8,
                    status="pending",
                    user_id="user-niyi-topic-conflict",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        audit = NiyiEngine(
            principal=Principal(user_id="user-niyi-topic-conflict", tenant_id="tenant-a")
        ).audit_pending_proposals(limit=10)

        assert audit["total_pending"] == 2
        assert audit["conflicting_pairs"] == []

    def test_seam8_overlapping_field_with_different_values_is_mutex(self):
        mem, _ = _seed_memory_with_edges(user_id="user-niyi-patch-conflict")
        t = utcnow()
        with db.get_session() as s:
            for proposal_id, content in (
                ("prop-niyi-patch-a", "每分钟六十次"),
                ("prop-niyi-patch-b", "每分钟一百次"),
            ):
                s.add(
                    MemoryProposal(
                        id=proposal_id,
                        proposal_type="update",
                        target_memory_id=mem.id,
                        reason=f"限流阈值调整为{content}",
                        proposed_patch={"content": content},
                        confidence=0.8,
                        status="pending",
                        user_id="user-niyi-patch-conflict",
                        tenant_id="tenant-a",
                        created_at=t,
                    )
                )
            s.commit()

        audit = NiyiEngine(
            principal=Principal(user_id="user-niyi-patch-conflict", tenant_id="tenant-a")
        ).audit_pending_proposals(limit=10)

        assert len(audit["conflicting_pairs"]) == 1
        conflict = audit["conflicting_pairs"][0]
        assert conflict["conflict_kind"] == "overlapping_patch_values"
        assert conflict["conflicting_fields"] == ["content"]

    def test_seam9_edge_to_another_owner_is_excluded_from_impact_details(self):
        mem, _ = _seed_memory_with_edges(user_id="user-niyi-edge-owner")
        t = utcnow()
        outsider_id = f"mem-niyi-outsider-{t.timestamp()}"
        edge_id = f"edge-niyi-outsider-{t.timestamp()}"
        with db.get_session() as s:
            s.add(
                MemoryItem(
                    id=outsider_id,
                    content="他人私有下游事实",
                    confidence=0.8,
                    domain="user",
                    lane="rule",
                    status="active",
                    user_id="user-niyi-edge-outsider",
                    tenant_id="tenant-a",
                    created_at=t,
                    updated_at=t,
                )
            )
            s.add(
                MemoryEdge(
                    id=edge_id,
                    source_memory_id=mem.id,
                    target_memory_id=outsider_id,
                    relation="causes",
                    confidence=0.9,
                    reason="不得对调用者暴露的边说明",
                    user_id="user-niyi-edge-owner",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id="user-niyi-edge-owner", tenant_id="tenant-a")
        ).deliberate_proposal("update", {"content": "新内容"}, mem.id)

        assert edge_id not in {detail["edge_id"] for detail in outcome["impacted_edge_details"]}
        assert outsider_id not in outcome["topology_impact"]["downstream_node_ids"]

    def test_seam10_hidden_causal_dependency_marks_analysis_incomplete_and_high_risk(self):
        t = utcnow()
        user_id = "user-niyi-hidden-dependency"
        target_id = f"mem-niyi-hidden-target-{t.timestamp()}"
        hidden_id = f"mem-niyi-hidden-node-{t.timestamp()}"
        hidden_edge_id = f"edge-niyi-hidden-{t.timestamp()}"
        with db.get_session() as s:
            s.add(
                MemoryItem(
                    id=target_id,
                    content="可见目标记忆",
                    confidence=0.8,
                    domain="user",
                    lane="rule",
                    status="active",
                    user_id=user_id,
                    tenant_id="tenant-a",
                    created_at=t,
                    updated_at=t,
                )
            )
            s.add(
                MemoryItem(
                    id=hidden_id,
                    content="不可见的下游事实",
                    confidence=0.8,
                    domain="user",
                    lane="rule",
                    status="active",
                    user_id="another-user",
                    tenant_id="tenant-a",
                    created_at=t,
                    updated_at=t,
                )
            )
            s.add(
                MemoryEdge(
                    id=hidden_edge_id,
                    source_memory_id=target_id,
                    target_memory_id=hidden_id,
                    relation="causes",
                    confidence=0.9,
                    reason="不可见依赖的关系说明",
                    user_id=user_id,
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id=user_id, tenant_id="tenant-a")
        ).deliberate_proposal("deprecate", {"status": "deprecated"}, target_id)

        topology = outcome["topology_impact"]
        assert outcome["risk_level"] == "high"
        assert topology["analysis_complete"] is False
        assert topology["hidden_dependency_detected"] is True
        assert hidden_id not in str(outcome)
        assert hidden_edge_id not in {
            detail["edge_id"] for detail in outcome["impacted_edge_details"]
        }

    def test_seam11_content_update_projects_state_and_marks_descendants_for_review(self):
        mem, edges = _seed_memory_with_edges(user_id="user-niyi-update-projection")

        outcome = NiyiEngine(
            principal=Principal(user_id="user-niyi-update-projection", tenant_id="tenant-a")
        ).deliberate_proposal(
            "update",
            {"content": "修订后的限流因果事实"},
            mem.id,
        )

        patch_effect = outcome["topology_impact"]["patch_effect"]
        assert patch_effect["projected_target_state"]["content"] == "修订后的限流因果事实"
        assert patch_effect["changed_fields"] == ["content"]
        assert patch_effect["downstream_review_node_ids"] == sorted(
            edge.target_memory_id for edge in edges
        )
        assert patch_effect["semantic_review_required"] is True

    def test_seam12_noncausal_relation_does_not_raise_retirement_to_high(self):
        t = utcnow()
        user_id = "user-niyi-noncausal-retirement"
        target_id = f"mem-niyi-noncausal-target-{t.timestamp()}"
        related_id = f"mem-niyi-noncausal-related-{t.timestamp()}"
        with db.get_session() as s:
            for memory_id, content in (
                (target_id, "目标记忆"),
                (related_id, "普通关联记忆"),
            ):
                s.add(
                    MemoryItem(
                        id=memory_id,
                        content=content,
                        confidence=0.8,
                        domain="user",
                        lane="rule",
                        status="active",
                        user_id=user_id,
                        tenant_id="tenant-a",
                        created_at=t,
                        updated_at=t,
                    )
                )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-noncausal-{t.timestamp()}",
                    source_memory_id=target_id,
                    target_memory_id=related_id,
                    relation="references",
                    confidence=0.9,
                    reason="普通引用关系",
                    user_id=user_id,
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id=user_id, tenant_id="tenant-a")
        ).deliberate_proposal("deprecate", {"status": "deprecated"}, target_id)

        assert outcome["impacted_edges_count"] == 1
        assert outcome["topology_impact"]["direct_causal_edges_count"] == 0
        assert outcome["risk_level"] != "high"

    def test_seam13_merge_simulates_archived_source_topology(self):
        target, _ = _seed_memory_with_edges(user_id="user-niyi-merge-projection")
        t = utcnow()
        source_id = f"mem-niyi-merge-source-{t.timestamp()}"
        source_child_id = f"mem-niyi-merge-source-child-{t.timestamp()}"
        with db.get_session() as s:
            for memory_id, content in (
                (source_id, "待合并的旧限流事实"),
                (source_child_id, "旧事实支撑的下游指标"),
            ):
                s.add(
                    MemoryItem(
                        id=memory_id,
                        content=content,
                        confidence=0.8,
                        domain="user",
                        lane="rule",
                        status="active",
                        user_id="user-niyi-merge-projection",
                        tenant_id="tenant-a",
                        created_at=t,
                        updated_at=t,
                    )
                )
            s.add(
                MemoryEdge(
                    id=f"edge-niyi-merge-source-{t.timestamp()}",
                    source_memory_id=source_id,
                    target_memory_id=source_child_id,
                    relation="causes",
                    confidence=0.9,
                    reason="旧事实导致下游指标",
                    user_id="user-niyi-merge-projection",
                    tenant_id="tenant-a",
                    created_at=t,
                )
            )
            s.commit()

        outcome = NiyiEngine(
            principal=Principal(user_id="user-niyi-merge-projection", tenant_id="tenant-a")
        ).deliberate_proposal(
            "merge",
            {
                "content": "合并后的限流事实",
                "evidence_ids": [target.id, source_id],
            },
            target.id,
        )

        topology = outcome["topology_impact"]
        assert topology["merge_source_ids"] == [source_id]
        assert topology["patch_effect"]["retired_memory_ids"] == [source_id]
        assert source_child_id in topology["disconnected_node_ids"]
        assert topology["direct_causal_edges_count"] >= 3
        assert outcome["risk_level"] == "medium"
