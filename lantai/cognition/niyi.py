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

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.storage import db


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
            "inspected_at": self.inspected_at.isoformat(),
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
        accessible_edges = [
            e for e in all_edges if self._check_access(owner_id=e.user_id, tenant_id=e.tenant_id)
        ]

        impacted_count = len(accessible_edges)
        edge_details = [
            {
                "edge_id": e.id,
                "relation": e.relation,
                "source": e.source_memory_id,
                "target": e.target_memory_id,
                "reason": e.reason,
            }
            for e in accessible_edges
        ]

        before_state = {
            "content": mem.content,
            "confidence": mem.confidence,
            "status": mem.status,
            "lane": mem.lane,
        }

        # 计算置信度浮动与风险评级
        target_conf = patch_data.get("confidence", mem.confidence)
        conf_delta = float(target_conf) - float(mem.confidence)

        if proposal_type == "deprecate":
            if impacted_count > 0:
                risk_level = "high"
                delib_reason = (
                    f"废弃记忆包含 {impacted_count} 条因果与支撑拓扑边，"
                    "操作可能导致下游断言悬空与推理链条断裂"
                )
            else:
                risk_level = "medium"
                delib_reason = "废弃孤立记忆，无直接关联拓扑边，风险可控"
        elif proposal_type in ("update", "merge"):
            if impacted_count > 0:
                risk_level = "medium"
                delib_reason = (
                    f"更新涉及 {impacted_count} 条既有关联边，建议复核语义一致性以防因果关系漂移"
                )
            else:
                risk_level = "low"
                delib_reason = "内容修正无下游拓扑依赖，风险较低"
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
        )
        return outcome.to_dict(fenced=True)

    def audit_pending_proposals(self, limit: int = 50) -> dict[str, Any]:
        """审计未决提案池，识别互斥与竞争冲突对。"""
        if self.session is not None:
            return self._audit_pending(self.session, limit)
        with db.get_session() as s:
            return self._audit_pending(s, limit)

    def _audit_pending(self, s: Session, limit: int) -> dict[str, Any]:
        stmt = (
            select(MemoryProposal)
            .where(MemoryProposal.status == "pending")
            .order_by(MemoryProposal.created_at.desc())
        )
        props = s.exec(stmt).all()

        accessible_props = [
            p for p in props if self._check_access(owner_id=p.user_id, tenant_id=p.tenant_id)
        ][:limit]

        # 聚类相同 target_memory_id 的未决提案
        target_map: dict[str, list[MemoryProposal]] = {}
        for p in accessible_props:
            tid = p.target_memory_id or "NONE"
            target_map.setdefault(tid, []).append(p)

        conflicting_pairs: list[dict[str, Any]] = []
        risk_dist = {"low": 0, "medium": 0, "high": 0}

        for tid, plist in target_map.items():
            if tid == "NONE":
                continue
            if len(plist) > 1:
                # 存在竞争提案
                for i in range(len(plist)):
                    for j in range(i + 1, len(plist)):
                        p_a = plist[i]
                        p_b = plist[j]
                        conflicting_pairs.append(
                            {
                                "target_memory_id": tid,
                                "proposal_id_a": p_a.id,
                                "proposal_id_b": p_b.id,
                                "type_a": p_a.proposal_type,
                                "type_b": p_b.proposal_type,
                                "description": (
                                    f"针对同一记忆({tid})并发存在 {p_a.proposal_type} 与 "
                                    f"{p_b.proposal_type} 互斥竞争提案，需人工或特权裁决"
                                ),
                            }
                        )

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
