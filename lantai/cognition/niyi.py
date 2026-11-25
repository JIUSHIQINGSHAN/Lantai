"""拟议（Niyi）认知演化提案审议与因果变化推演引擎 (ADR-0071)。

出处：《易·系辞上》「拟之而后言，议之而后动，拟议以成其变化。」
职责：
1. 提案因果拓扑推演（deliberate_proposal）：在变更落实前模拟对下游因果边与断言的影响；
2. 提案断裂与漂移风险评级（risk_level: low / medium / high）；
3. 未决提案池冲突与互斥审计（audit_pending_proposals）；
4. 全链路贯穿樊篱（wrap_as_data）防注入围栏；
5. 落实多租户行级 ACL 隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.storage import db

_TOPOLOGY_RELATIONS = ("causes", "leads_to", "supports", "refines", "triggered_by")
_RETIREMENT_STATUSES = frozenset(
    {"archived", "deleted", "deprecated", "inactive", "retired", "superseded"}
)
_MAX_TOPOLOGY_NODES = 256
_MAX_TOPOLOGY_EDGES = 1024
_MAX_PENDING_AUDIT_LIMIT = 200


@dataclass
class DeliberationOutcome:
    """拟议推演与审议结果契约。"""

    proposal_type: str  # add | update | merge | deprecate
    target_memory_id: str | None
    before_state: dict[str, Any]
    after_patch: dict[str, Any]
    impacted_edges_count: int
    impacted_edge_details: list[dict[str, Any]]
    risk_level: str  # low | medium | high
    deliberation_reason: str
    confidence_delta: float = 0.0
    user_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    topology_impact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        fenced_r = (
            wrap_as_data(
                self.deliberation_reason,
                item_id=self.target_memory_id or f"prop-{self.proposal_type}",
                score=1.0 if self.risk_level == "low" else 0.5,
            )
            if fenced
            else self.deliberation_reason
        )
        return {
            "proposal_type": self.proposal_type,
            "target_memory_id": self.target_memory_id,
            "before_state": self.before_state,
            "after_patch": self.after_patch,
            "impacted_edges_count": self.impacted_edges_count,
            "impacted_edge_details": self.impacted_edge_details,
            "risk_level": self.risk_level,
            "deliberation_reason": self.deliberation_reason,
            "fenced_reason": fenced_r,
            "confidence_delta": round(self.confidence_delta, 4),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
            "topology_impact": self.topology_impact,
        }


@dataclass
class AuditReport:
    """未决提案审计汇总契约。"""

    total_pending: int
    conflicting_pairs: list[dict[str, Any]]
    risk_distribution: dict[str, int]
    audit_summary: str
    inspected_at: datetime = field(default_factory=utcnow)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        fenced_s = (
            wrap_as_data(
                self.audit_summary,
                item_id=f"audit-{int(self.inspected_at.timestamp())}",
                score=0.9,
            )
            if fenced
            else self.audit_summary
        )
        return {
            "total_pending": self.total_pending,
            "conflicting_pairs": self.conflicting_pairs,
            "risk_distribution": self.risk_distribution,
            "audit_summary": self.audit_summary,
            "fenced_summary": fenced_s,
            "inspected_at": iso_utc(self.inspected_at),
        }


class NiyiEngine:
    """拟议（Niyi）核心演化审议与因果变化推演引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
        shared_scope: str = "private",
    ) -> bool:
        """行级安全隔离校验（宁 miss 不脏写）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        if shared_scope == "swarm_shared":
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    @staticmethod
    def _load_node_cache(
        s: Session, memory_ids: set[str], node_cache: dict[str, MemoryItem | None]
    ) -> None:
        missing = memory_ids - node_cache.keys()
        if not missing:
            return
        nodes = s.exec(select(MemoryItem).where(col(MemoryItem.id).in_(missing))).all()
        node_cache.update({node.id: node for node in nodes})
        node_cache.update({memory_id: None for memory_id in missing - {node.id for node in nodes}})

    def _edge_visibility(
        self,
        s: Session,
        edges: Sequence[MemoryEdge],
        node_cache: dict[str, MemoryItem | None],
    ) -> dict[str, str]:
        endpoint_ids = {
            memory_id
            for edge in edges
            for memory_id in (edge.source_memory_id, edge.target_memory_id)
        }
        self._load_node_cache(s, endpoint_ids, node_cache)
        visibility: dict[str, str] = {}
        for edge in edges:
            if not self._check_access(owner_id=edge.user_id, tenant_id=edge.tenant_id):
                visibility[edge.id] = "hidden"
                continue
            endpoints = [
                node_cache[memory_id]
                for memory_id in (edge.source_memory_id, edge.target_memory_id)
            ]
            if any(endpoint is None for endpoint in endpoints):
                visibility[edge.id] = "unresolved"
                continue
            if any(
                not self._check_access(
                    owner_id=endpoint.user_id,
                    tenant_id=endpoint.tenant_id,
                    shared_scope=getattr(endpoint, "shared_scope", "private"),
                )
                for endpoint in endpoints
                if endpoint is not None
            ):
                visibility[edge.id] = "hidden"
                continue
            visibility[edge.id] = "accessible"
        return visibility

    def _topology_impact(
        self,
        s: Session,
        target_memory_id: str,
        direct_edges: Sequence[MemoryEdge],
        *,
        removing_target: bool,
    ) -> dict[str, Any]:
        """Trace the accessible downstream component and simulate target removal."""
        topology_edges: dict[str, MemoryEdge] = {}
        downstream_depth: dict[str, int] = {}
        unresolved_node_ids: set[str] = set()
        hidden_dependency_detected = False
        node_cache: dict[str, MemoryItem | None] = {}
        direct_visibility = self._edge_visibility(s, direct_edges, node_cache)
        for edge in direct_edges:
            if edge.relation not in _TOPOLOGY_RELATIONS:
                continue
            visibility = direct_visibility[edge.id]
            if visibility == "hidden":
                hidden_dependency_detected = True
            elif visibility == "unresolved":
                unresolved_node_ids.update(
                    memory_id
                    for memory_id in (edge.source_memory_id, edge.target_memory_id)
                    if node_cache[memory_id] is None
                )

        frontier = [target_memory_id]
        visited = {target_memory_id}
        truncated = False

        while frontier:
            if len(topology_edges) >= _MAX_TOPOLOGY_EDGES:
                truncated = True
                break
            remaining_edge_budget = _MAX_TOPOLOGY_EDGES - len(topology_edges)
            stmt = select(MemoryEdge).where(
                (
                    col(MemoryEdge.source_memory_id).in_(frontier)
                    & col(MemoryEdge.relation).in_(("causes", "leads_to", "supports", "refines"))
                )
                | (
                    col(MemoryEdge.target_memory_id).in_(frontier)
                    & (MemoryEdge.relation == "triggered_by")
                )
            )
            rows = s.exec(stmt.limit(remaining_edge_budget + 1)).all()
            if len(rows) > remaining_edge_budget:
                truncated = True
                rows = rows[:remaining_edge_budget]
            row_visibility = self._edge_visibility(s, rows, node_cache)
            next_frontier: list[str] = []
            for edge in rows:
                child_id = (
                    edge.source_memory_id
                    if edge.relation == "triggered_by"
                    else edge.target_memory_id
                )
                edge_visibility = row_visibility[edge.id]
                if edge_visibility != "accessible":
                    if edge.relation in _TOPOLOGY_RELATIONS:
                        if edge_visibility == "unresolved":
                            unresolved_node_ids.update(
                                memory_id
                                for memory_id in (edge.source_memory_id, edge.target_memory_id)
                                if node_cache[memory_id] is None
                            )
                        else:
                            hidden_dependency_detected = True
                    continue
                topology_edges[edge.id] = edge
                child = node_cache[child_id]
                if child is None:
                    unresolved_node_ids.add(child_id)
                    continue
                if child.status != "active":
                    continue
                if child_id in visited:
                    continue
                if len(downstream_depth) >= _MAX_TOPOLOGY_NODES:
                    truncated = True
                    continue
                visited.add(child_id)
                downstream_depth[child_id] = len(downstream_depth) + 1
                next_frontier.append(child_id)
            frontier = next_frontier

        downstream_ids = set(downstream_depth)
        disconnected_ids: set[str] = set()
        if removing_target and downstream_ids:
            incoming_stmt = select(MemoryEdge).where(
                col(MemoryEdge.relation).in_(_TOPOLOGY_RELATIONS),
                (
                    col(MemoryEdge.target_memory_id).in_(downstream_ids)
                    & col(MemoryEdge.relation).in_(("causes", "leads_to", "supports", "refines"))
                )
                | (
                    col(MemoryEdge.source_memory_id).in_(downstream_ids)
                    & (MemoryEdge.relation == "triggered_by")
                ),
            )
            incoming_by_node: dict[str, list[MemoryEdge]] = {
                memory_id: [] for memory_id in downstream_ids
            }
            incoming_rows = s.exec(incoming_stmt.limit(_MAX_TOPOLOGY_NODES * 4 + 1)).all()
            if len(incoming_rows) > _MAX_TOPOLOGY_NODES * 4:
                truncated = True
                incoming_rows = incoming_rows[: _MAX_TOPOLOGY_NODES * 4]
            incoming_visibility = self._edge_visibility(s, incoming_rows, node_cache)
            for edge in incoming_rows:
                edge_visibility = incoming_visibility[edge.id]
                if edge_visibility == "accessible":
                    dependent_id = (
                        edge.source_memory_id
                        if edge.relation == "triggered_by"
                        else edge.target_memory_id
                    )
                    if dependent_id in incoming_by_node:
                        incoming_by_node[dependent_id].append(edge)
                elif edge.relation in _TOPOLOGY_RELATIONS:
                    if edge_visibility == "unresolved":
                        unresolved_node_ids.update(
                            memory_id
                            for memory_id in (edge.source_memory_id, edge.target_memory_id)
                            if node_cache[memory_id] is None
                        )
                    else:
                        hidden_dependency_detected = True

            # Nodes with an active incoming edge from outside the removed component are
            # surviving roots; walk forward from them to preserve alternate paths/cycles.
            surviving = {
                memory_id
                for memory_id, edges in incoming_by_node.items()
                if any(
                    (
                        edge.target_memory_id
                        if edge.relation == "triggered_by"
                        else edge.source_memory_id
                    )
                    != target_memory_id
                    and (
                        edge.target_memory_id
                        if edge.relation == "triggered_by"
                        else edge.source_memory_id
                    )
                    not in downstream_ids
                    and self._active_accessible_node(
                        s,
                        edge.target_memory_id
                        if edge.relation == "triggered_by"
                        else edge.source_memory_id,
                        node_cache,
                    )
                    for edge in edges
                )
            }
            adjacency: dict[str, set[str]] = {}
            for edge in topology_edges.values():
                source_id, child_id = (
                    (edge.target_memory_id, edge.source_memory_id)
                    if edge.relation == "triggered_by"
                    else (edge.source_memory_id, edge.target_memory_id)
                )
                if source_id != target_memory_id:
                    adjacency.setdefault(source_id, set()).add(child_id)
            pending = deque(surviving)
            while pending:
                node_id = pending.popleft()
                for child_id in adjacency.get(node_id, set()) & downstream_ids:
                    if child_id not in surviving:
                        surviving.add(child_id)
                        pending.append(child_id)
            disconnected_ids = downstream_ids - surviving
        elif removing_target:
            disconnected_ids = set()

        direct_causal_edges = [
            edge
            for edge in direct_edges
            if edge.relation in _TOPOLOGY_RELATIONS
            and target_memory_id in (edge.source_memory_id, edge.target_memory_id)
            and direct_visibility[edge.id] == "accessible"
        ]
        return {
            "direct_causal_edges_count": len(direct_causal_edges),
            "direct_causal_edge_ids": sorted(edge.id for edge in direct_causal_edges),
            "downstream_edge_count": len(topology_edges),
            "downstream_edge_ids": sorted(topology_edges),
            "downstream_node_ids": sorted(downstream_ids),
            "disconnected_node_ids": sorted(disconnected_ids),
            "unresolved_node_ids": sorted(unresolved_node_ids),
            "unresolved_node_count": len(unresolved_node_ids),
            "hidden_dependency_detected": hidden_dependency_detected,
            "analysis_complete": (
                not truncated and not unresolved_node_ids and not hidden_dependency_detected
            ),
        }

    def _active_accessible_node(
        self,
        s: Session,
        memory_id: str,
        node_cache: dict[str, MemoryItem | None] | None = None,
    ) -> bool:
        node = node_cache.get(memory_id) if node_cache is not None else s.get(MemoryItem, memory_id)
        return bool(
            node
            and node.status == "active"
            and self._check_access(
                owner_id=node.user_id,
                tenant_id=node.tenant_id,
                shared_scope=getattr(node, "shared_scope", "private"),
            )
        )

    @staticmethod
    def _merge_source_ids(patch_data: dict[str, Any]) -> list[str]:
        """Read merge sources from the additive dry-run patch shape."""
        raw_values: list[Any] = []
        for key in ("evidence_ids", "source_memory_ids", "merge_memory_ids"):
            value = patch_data.get(key)
            if isinstance(value, (list, tuple, set)):
                raw_values.extend(value)
        for key in ("source_memory_id", "merge_source_id"):
            value = patch_data.get(key)
            if value:
                raw_values.append(value)
        return list(
            dict.fromkeys(value for value in raw_values if isinstance(value, str) and value)
        )

    @staticmethod
    def _combine_topology_impact(base: dict[str, Any], extra: dict[str, Any]) -> None:
        for key in (
            "downstream_node_ids",
            "disconnected_node_ids",
            "unresolved_node_ids",
            "direct_causal_edge_ids",
            "downstream_edge_ids",
        ):
            base[key] = sorted(set(base.get(key, [])) | set(extra.get(key, [])))
        base["direct_causal_edges_count"] = len(base["direct_causal_edge_ids"])
        base["downstream_edge_count"] = len(base["downstream_edge_ids"])
        base["unresolved_node_count"] = len(base["unresolved_node_ids"])
        base["hidden_dependency_detected"] = bool(
            base.get("hidden_dependency_detected") or extra.get("hidden_dependency_detected")
        )
        base["analysis_complete"] = bool(
            base.get("analysis_complete") and extra.get("analysis_complete")
        )

    @staticmethod
    def _conflict_kind(
        first: MemoryProposal, second: MemoryProposal, *, same_subject: bool
    ) -> tuple[str, list[str]] | None:
        types = {first.proposal_type, second.proposal_type}
        if "deprecate" in types and len(types) > 1:
            return "lifecycle_conflict", []
        if "merge" in types and len(types) > 1:
            return "structural_conflict", []
        if first.proposal_type == second.proposal_type == "merge" and same_subject:
            return "concurrent_merge", []

        first_patch = first.proposed_patch if isinstance(first.proposed_patch, dict) else {}
        second_patch = second.proposed_patch if isinstance(second.proposed_patch, dict) else {}
        differing_fields = sorted(
            key
            for key in first_patch.keys() & second_patch.keys()
            if first_patch[key] != second_patch[key]
        )
        if differing_fields:
            return "overlapping_patch_values", differing_fields
        return None

    def deliberate_proposal(
        self,
        proposal_type: str,
        patch_data: dict[str, Any],
        target_memory_id: str | None = None,
    ) -> dict[str, Any]:
        """模拟推演提案实施对系统拓扑与认知状态的影响。"""
        if self.session is not None:
            return self._deliberate(self.session, proposal_type, patch_data, target_memory_id)
        with db.get_session() as s:
            return self._deliberate(s, proposal_type, patch_data, target_memory_id)

    def _deliberate(
        self,
        s: Session,
        proposal_type: str,
        patch_data: dict[str, Any],
        target_memory_id: str | None,
    ) -> dict[str, Any]:
        viewer = viewer_of(self.principal) if self.principal else None
        current_tenant = getattr(self.principal, "tenant_id", None) if self.principal else None

        # 1. 针对新增提案
        if proposal_type == "add" or not target_memory_id:
            reason = "新增认知事实，未影响既有因果拓扑，演化风险极低"
            outcome = DeliberationOutcome(
                proposal_type=proposal_type,
                target_memory_id=target_memory_id,
                before_state={},
                after_patch=patch_data,
                impacted_edges_count=0,
                impacted_edge_details=[],
                risk_level="low",
                deliberation_reason=reason,
                confidence_delta=0.0,
                user_id=viewer,
                tenant_id=current_tenant,
            )
            return outcome.to_dict(fenced=True)

        # 2. 针对指定目标记忆的演化（update / merge / deprecate）
        mem = s.get(MemoryItem, target_memory_id)
        if not mem or not self._check_access(
            owner_id=mem.user_id,
            tenant_id=mem.tenant_id,
            shared_scope=getattr(mem, "shared_scope", "private"),
        ):
            # 目标不存在或无权限访问
            return DeliberationOutcome(
                proposal_type=proposal_type,
                target_memory_id=target_memory_id,
                before_state={},
                after_patch=patch_data,
                impacted_edges_count=0,
                impacted_edge_details=[],
                risk_level="low",
                deliberation_reason="未找到目标记忆或无权访问，推演终止",
                confidence_delta=0.0,
                user_id=viewer,
                tenant_id=current_tenant,
            ).to_dict(fenced=True)

        # 检索以 target_memory_id 为起点的下游因果边
        edge_stmt = select(MemoryEdge).where(
            (MemoryEdge.source_memory_id == target_memory_id)
            | (MemoryEdge.target_memory_id == target_memory_id)
        )
        all_edges = s.exec(edge_stmt).all()
        edge_visibility = self._edge_visibility(s, all_edges, {})
        accessible_edges = [e for e in all_edges if edge_visibility[e.id] == "accessible"]
        edge_owner_visible = [
            e for e in all_edges if self._check_access(owner_id=e.user_id, tenant_id=e.tenant_id)
        ]

        # Keep the historical count shape for callers, while details and topology
        # remain fail-closed when an endpoint is hidden or unresolved.
        impacted_count = len(edge_owner_visible)
        edge_details = [
            {
                "edge_id": e.id,
                "relation": e.relation,
                "source": e.source_memory_id,
                "target": e.target_memory_id,
                "reason": e.reason,
                "fenced_reason": wrap_as_data(
                    e.reason,
                    item_id=e.id,
                    score=round(float(e.confidence or 0.5), 4),
                ),
            }
            for e in accessible_edges
        ]

        retiring_target = (
            proposal_type == "deprecate"
            or patch_data.get("status") in _RETIREMENT_STATUSES
            or patch_data.get("lifecycle_status") in _RETIREMENT_STATUSES
        )
        topology_impact = self._topology_impact(
            s,
            target_memory_id,
            all_edges,
            removing_target=retiring_target,
        )

        merge_source_ids = (
            [
                source_id
                for source_id in self._merge_source_ids(patch_data)
                if source_id != target_memory_id
            ]
            if proposal_type == "merge"
            else []
        )
        merge_source_impacts: list[dict[str, Any]] = []
        for source_id in merge_source_ids:
            if source_id == target_memory_id:
                continue
            source = s.get(MemoryItem, source_id)
            if source is None or not self._check_access(
                owner_id=getattr(source, "user_id", None),
                tenant_id=getattr(source, "tenant_id", None),
                shared_scope=getattr(source, "shared_scope", "private"),
            ):
                topology_impact["unresolved_node_ids"] = sorted(
                    set(topology_impact.get("unresolved_node_ids", [])) | {source_id}
                )
                topology_impact["unresolved_node_count"] = len(
                    topology_impact["unresolved_node_ids"]
                )
                topology_impact["analysis_complete"] = False
                merge_source_impacts.append(
                    {"source_memory_id": source_id, "analysis_complete": False}
                )
                continue
            source_edges = s.exec(
                select(MemoryEdge).where(
                    (MemoryEdge.source_memory_id == source_id)
                    | (MemoryEdge.target_memory_id == source_id)
                )
            ).all()
            source_impact = self._topology_impact(
                s,
                source_id,
                source_edges,
                removing_target=True,
            )
            self._combine_topology_impact(topology_impact, source_impact)
            merge_source_impacts.append({"source_memory_id": source_id, **source_impact})
        topology_impact["merge_source_ids"] = merge_source_ids
        topology_impact["merge_source_impacts"] = merge_source_impacts

        before_state = {
            "content": mem.content,
            "confidence": mem.confidence,
            "status": mem.status,
            "lane": mem.lane,
            "memory_type": mem.memory_type,
            "domain": mem.domain,
            "structure": mem.structure,
            "tags": mem.tags,
            "importance": mem.importance,
            "lifecycle_status": mem.lifecycle_status,
        }

        # 计算置信度浮动与风险评级
        target_conf = patch_data.get("confidence", mem.confidence)
        # 票 68：先验后用——裸 float() 对 "high" 报「could not convert」
        # （调用方无从自查，REST 面炸 500），1.5/-0.2 越界静默吃进风险评级。
        # 拒绝而非静默钳制（宁 miss 不脏写）。
        try:
            target_conf_f = float(target_conf)
        except (TypeError, ValueError):
            raise ValueError(f"confidence 须为 [0, 1] 内数值，收到 {target_conf!r}") from None
        if not 0.0 <= target_conf_f <= 1.0:
            raise ValueError(f"confidence 须为 [0, 1] 内数值，收到 {target_conf!r}")
        conf_delta = target_conf_f - float(mem.confidence)

        projected_target_state = dict(before_state)
        changed_fields: list[str] = []
        for field_name, value in patch_data.items():
            if field_name == "confidence":
                value = target_conf_f
            if (
                field_name not in projected_target_state
                or projected_target_state[field_name] != value
            ):
                changed_fields.append(field_name)
            if field_name in projected_target_state:
                projected_target_state[field_name] = value
        causal_downstream_ids = topology_impact["downstream_node_ids"]
        semantic_fields = {
            "content",
            "confidence",
            "domain",
            "lane",
            "memory_type",
            "structure",
            "tags",
            "role",
            "lifecycle_status",
            "status",
        }
        semantic_review_required = proposal_type == "merge" or bool(
            set(changed_fields) & semantic_fields
        )
        patch_effect = {
            "changed_fields": sorted(changed_fields),
            "projected_target_state": projected_target_state,
            "semantic_review_required": semantic_review_required,
            "merge_source_ids": merge_source_ids,
            "retired_memory_ids": merge_source_ids if proposal_type == "merge" else [],
            "downstream_review_node_ids": sorted(causal_downstream_ids)
            if semantic_review_required
            else [],
        }
        topology_impact["patch_effect"] = patch_effect

        if retiring_target:
            disconnected_count = len(topology_impact["disconnected_node_ids"])
            direct_causal_count = topology_impact["direct_causal_edges_count"]
            if (
                direct_causal_count
                or disconnected_count
                or topology_impact["hidden_dependency_detected"]
                or not topology_impact["analysis_complete"]
            ):
                risk_level = "high"
                delib_reason = (
                    f"直接因果边 {direct_causal_count} 条、模拟后失去路径的下游节点 {disconnected_count} 个，"
                    "或拓扑遍历触及预算上限，存在断言悬空与推理链条断裂风险"
                )
            elif topology_impact["downstream_node_ids"]:
                risk_level = "medium"
                delib_reason = (
                    f"目标有 {len(topology_impact['downstream_node_ids'])} 个下游节点，"
                    "但当前可见拓扑中存在替代路径；仍需复核相关关系"
                )
            else:
                risk_level = "medium"
                delib_reason = "废弃孤立记忆，无直接关联拓扑边，风险可控"
        elif proposal_type in ("update", "merge"):
            direct_causal_count = topology_impact["direct_causal_edges_count"]
            if semantic_review_required and (
                direct_causal_count
                or topology_impact["hidden_dependency_detected"]
                or not topology_impact["analysis_complete"]
            ):
                if conf_delta <= -0.25:
                    risk_level = "high"
                    delib_reason = (
                        f"置信度下降 {abs(conf_delta):.2f} 且存在 {direct_causal_count} 条直接因果边，"
                        "下游因果断言可能失去足够支撑"
                    )
                else:
                    risk_level = "medium"
                    delib_reason = (
                        f"更新涉及 {direct_causal_count} 条直接因果边、"
                        f"{len(topology_impact['downstream_node_ids'])} 个下游节点，"
                        "建议复核语义一致性以防因果关系漂移"
                    )
            else:
                risk_level = "low"
                delib_reason = "内容修正无可见下游因果依赖，风险较低"
        else:
            risk_level = "low"
            delib_reason = f"常规提案推演，影响边数量: {impacted_count}"

        outcome = DeliberationOutcome(
            proposal_type=proposal_type,
            target_memory_id=target_memory_id,
            before_state=before_state,
            after_patch=patch_data,
            impacted_edges_count=impacted_count,
            impacted_edge_details=edge_details,
            risk_level=risk_level,
            deliberation_reason=delib_reason,
            confidence_delta=conf_delta,
            user_id=mem.user_id,
            tenant_id=mem.tenant_id,
            topology_impact=topology_impact,
            metadata={
                "changed_fields": sorted(changed_fields),
                "topology_analysis_complete": topology_impact["analysis_complete"],
            },
        )
        return outcome.to_dict(fenced=True)

    def audit_pending_proposals(self, limit: int = 50) -> dict[str, Any]:
        """审计未决提案池，识别互斥与竞争冲突对。"""
        if self.session is not None:
            return self._audit_pending(self.session, limit)
        with db.get_session() as s:
            return self._audit_pending(s, limit)

    def _audit_pending(self, s: Session, limit: int) -> dict[str, Any]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 0 <= limit <= _MAX_PENDING_AUDIT_LIMIT
        ):
            raise ValueError(f"limit 须为 0..{_MAX_PENDING_AUDIT_LIMIT} 的整数")
        stmt = (
            select(MemoryProposal)
            .where(MemoryProposal.status == "pending")
            .order_by(col(MemoryProposal.created_at).desc())
        )
        if self.principal is not None and not bool(getattr(self.principal, "is_admin", False)):
            stmt = stmt.where(
                (col(MemoryProposal.user_id) == viewer_of(self.principal))
                | col(MemoryProposal.user_id).is_(None)
            )
            tenant_id = getattr(self.principal, "tenant_id", None)
            if tenant_id:
                stmt = stmt.where(
                    (MemoryProposal.tenant_id == tenant_id)
                    | col(MemoryProposal.tenant_id).is_(None)
                )
        props = s.exec(stmt.limit(limit)).all()

        accessible_props = [
            p for p in props if self._check_access(owner_id=p.user_id, tenant_id=p.tenant_id)
        ]

        # Group only identified targets: proposals without a target cannot be compared safely.
        target_map: dict[str, list[MemoryProposal]] = {}
        for p in accessible_props:
            if p.target_memory_id:
                target_map.setdefault(p.target_memory_id, []).append(p)

        conflicting_pairs: list[dict[str, Any]] = []
        risk_dist = {"low": 0, "medium": 0, "high": 0}

        def record_conflict(
            first: MemoryProposal,
            second: MemoryProposal,
            conflict_kind: str,
            conflicting_fields: list[str],
            target_memory_id: str | None,
        ) -> None:
            target = target_memory_id or "不同目标"
            description = f"{target}存在互斥待决提案，冲突类型：{conflict_kind}"
            if conflicting_fields:
                description += f"；字段：{', '.join(conflicting_fields)}"
            conflicting_pairs.append(
                {
                    "target_memory_id": target_memory_id,
                    "proposal_id_a": first.id,
                    "proposal_id_b": second.id,
                    "type_a": first.proposal_type,
                    "type_b": second.proposal_type,
                    "conflict_kind": conflict_kind,
                    "conflicting_fields": conflicting_fields,
                    "description": description,
                    "fenced_description": wrap_as_data(
                        description,
                        item_id=f"conflict-{first.id}-{second.id}",
                        score=0.9,
                    ),
                }
            )

        for tid, plist in target_map.items():
            for i, p_a in enumerate(plist):
                for p_b in plist[i + 1 :]:
                    conflict = self._conflict_kind(p_a, p_b, same_subject=True)
                    if conflict is None:
                        continue
                    conflict_kind, conflicting_fields = conflict
                    record_conflict(p_a, p_b, conflict_kind, conflicting_fields, tid)

        # 统计风险分布
        for p in accessible_props:
            if p.proposal_type == "deprecate":
                risk_dist["high"] += 1
            elif p.proposal_type in ("update", "merge"):
                risk_dist["medium"] += 1
            else:
                risk_dist["low"] += 1

        summary = (
            f"共审计 {len(accessible_props)} 个未决提案，"
            f"识别到 {len(conflicting_pairs)} 组互斥竞争提案，"
            f"高风险提案 {risk_dist['high']} 项"
        )

        report = AuditReport(
            total_pending=len(accessible_props),
            conflicting_pairs=conflicting_pairs,
            risk_distribution=risk_dist,
            audit_summary=summary,
        )
        return report.to_dict(fenced=True)
