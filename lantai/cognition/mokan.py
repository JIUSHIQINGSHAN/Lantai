"""磨勘（Mokan · 《宋史·选举志》「磨勘之法，核验资历功过以定升降」）：多源认知证据交叉勘验与可信度自证引擎 (ADR-0083)。

出处：《宋史·选举志》「磨勘之法，核验资历功过以定升降」，宋代设审官院、三班院核验官吏实绩功过与文书证据。
职责：
1. 建立结构化勘验契约 MokanVerificationItem 与 MokanAuditResult；
2. 纯函数可信度自证打分算法（TrustScore 计算）；
3. 多源异构事实交叉核验与互证支持链追踪（cross_verify_memories）；
4. 矛盾冲突检测、争议状态标记（disputed）与惩罚性降权（audit_contradictions）；
5. 落实行级租户与属主安全隔离（宁 miss 不越权）；
6. 全链路正文施加樊篱防注入围栏包裹（wrap_as_data）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, or_, select

from lantai.core.acl import SYSTEM_VIEWER, Principal, is_system_viewer, viewer_of
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage import db


@dataclass
class MokanVerificationItem:
    """磨勘单项证据勘验条目契约。"""

    memory_id: str
    content: str
    confidence_score: float
    verification_status: str  # verified / disputed / unverified
    cross_sources: list[str] = field(default_factory=list)
    contradiction_ids: list[str] = field(default_factory=list)
    trust_score: float = 0.5
    evidence_count: int = 1
    user_id: str | None = None
    tenant_id: str | None = None
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "memory_id": self.memory_id,
            "content": self.content,
            "confidence_score": round(self.confidence_score, 4),
            "verification_status": self.verification_status,
            "cross_sources": list(self.cross_sources),
            "contradiction_ids": list(self.contradiction_ids),
            "trust_score": round(self.trust_score, 4),
            "evidence_count": self.evidence_count,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class MokanAuditResult:
    """磨勘审计结果契约。"""

    query: str
    total_audited: int
    verified_count: int
    disputed_count: int
    items: list[MokanVerificationItem] = field(default_factory=list)
    summary: str = ""
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "query": self.query,
            "total_audited": self.total_audited,
            "verified_count": self.verified_count,
            "disputed_count": self.disputed_count,
            "items": [it.to_dict(fenced=fenced) for it in self.items],
            "summary": self.summary,
            "fenced_content": self.fenced_content if fenced else "",
        }


class MokanEngine:
    """磨勘·多源认知证据交叉勘验与可信度自证引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(self, *, owner_id: str | None, tenant_id: str | None) -> bool:
        """行级租户与属主安全校验（宁漏不越权、宁 miss 不脏写）。"""
        if getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal):
            return True

        viewer = viewer_of(self.principal)
        if viewer == SYSTEM_VIEWER:
            return True

        current_tenant = getattr(self.principal, "tenant_id", None)
        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return owner_id is None or owner_id == viewer

    @staticmethod
    def compute_trust_score(
        base_confidence: float,
        evidence_count: int = 1,
        contradiction_penalty: float = 0.0,
    ) -> float:
        """纯函数可信度自证打分算法（TrustScore）。

        公式设计原则：
        1. 证据数 = 1 且无惩罚时保持基础置信度（原汁原味）；
        2. 证据数 > 1 时产生边际递减的交叉印证增益；
        3. 存在矛盾冲突时，施加惩罚性降权；
        4. 结果严格截断保护在 [0.0, 1.0] 闭区间内。
        """
        base = max(0.0, min(1.0, float(base_confidence)))
        ev_count = max(0, int(evidence_count))

        if ev_count <= 1:
            score = base
        else:
            # 边际递减交叉增益：饱和度增长
            gain = (1.0 - base) * (1.0 - (1.0 / ev_count)) * 0.5
            score = base + gain

        if contradiction_penalty > 0.0:
            score -= float(contradiction_penalty)

        return max(0.0, min(1.0, round(score, 4)))

    def cross_verify_memories(
        self,
        memory_ids: list[str] | None = None,
        memories: list[MemoryItem] | None = None,
        session: Session | None = None,
    ) -> list[MokanVerificationItem]:
        """对一组记忆条目执行多源交叉勘验并输出勘验条目列表。"""
        s = session or self.session
        if s is None:
            with db.get_session() as auto_s:
                return self.cross_verify_memories(
                    memory_ids=memory_ids, memories=memories, session=auto_s
                )

        target_memories: list[MemoryItem] = []
        if memories:
            for m in memories:
                if self._check_access(owner_id=m.user_id, tenant_id=m.tenant_id):
                    target_memories.append(m)
        elif memory_ids:
            for mid in memory_ids:
                item = s.get(MemoryItem, mid)
                if item and self._check_access(owner_id=item.user_id, tenant_id=item.tenant_id):
                    target_memories.append(item)

        if not target_memories:
            return []

        active_ids = [m.id for m in target_memories if m.id]

        # 关联拓扑边（supports / refines / supersedes / contradicts）
        edges = s.exec(
            select(MemoryEdge).where(
                or_(
                    col(MemoryEdge.source_memory_id).in_(active_ids),
                    col(MemoryEdge.target_memory_id).in_(active_ids),
                )
            )
        ).all()

        # 租户安全过滤边
        safe_edges = [
            e for e in edges if self._check_access(owner_id=e.user_id, tenant_id=e.tenant_id)
        ]

        # 建立邻接关系索引
        supports_map: dict[str, set[str]] = {mid: set() for mid in active_ids}
        contradicts_map: dict[str, set[str]] = {mid: set() for mid in active_ids}

        for e in safe_edges:
            rel = (e.relation or "").lower()
            src = e.source_memory_id
            tgt = e.target_memory_id

            if rel in ("supports", "refines", "supersedes"):
                if src in supports_map and tgt:
                    supports_map[src].add(tgt)
                if tgt in supports_map and src:
                    supports_map[tgt].add(src)
            elif rel in ("contradicts", "conflicts"):
                if src in contradicts_map and tgt:
                    contradicts_map[src].add(tgt)
                if tgt in contradicts_map and src:
                    contradicts_map[tgt].add(src)

        results: list[MokanVerificationItem] = []
        for m in target_memories:
            mid = m.id
            contra_ids = sorted(contradicts_map.get(mid, set()))
            support_ids = sorted(supports_map.get(mid, set()))

            base_conf = float(m.confidence or 0.5)
            ev_count = 1 + len(support_ids)

            if contra_ids:
                status = "disputed"
                penalty = 0.35 + 0.05 * len(contra_ids)
            elif len(support_ids) > 0:
                status = "verified"
                penalty = 0.0
            else:
                status = "unverified"
                penalty = 0.0

            trust = self.compute_trust_score(
                base_confidence=base_conf,
                evidence_count=ev_count,
                contradiction_penalty=penalty,
            )

            fenced = wrap_as_data(
                m.content or "",
                item_id=mid,
                score=trust,
            )

            results.append(
                MokanVerificationItem(
                    memory_id=mid,
                    content=m.content or "",
                    confidence_score=base_conf,
                    verification_status=status,
                    cross_sources=support_ids,
                    contradiction_ids=contra_ids,
                    trust_score=trust,
                    evidence_count=ev_count,
                    user_id=m.user_id,
                    tenant_id=m.tenant_id,
                    fenced_content=fenced,
                )
            )

        return results

    def audit_contradictions(
        self,
        query: str = "",
        limit: int = 50,
        session: Session | None = None,
    ) -> MokanAuditResult:
        """审计存量事实的交叉一致性与未决矛盾冲突。"""
        s = session or self.session
        if s is None:
            with db.get_session() as auto_s:
                return self.audit_contradictions(query=query, limit=limit, session=auto_s)

        # 1. 检索候选记忆
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        if query and query.strip():
            stmt = stmt.where(col(MemoryItem.content).contains(query.strip()))
        candidate_items = s.exec(stmt.limit(limit)).all()

        # 2. 租户行级安全过滤
        permitted_memories = [
            m
            for m in candidate_items
            if self._check_access(owner_id=m.user_id, tenant_id=m.tenant_id)
        ]

        # 3. 如果通过 query 检索出的条目未包含完整的矛盾双方，尝试溯源关联的矛盾 MemoryEdge
        active_ids = {m.id for m in permitted_memories if m.id}
        contra_edges = s.exec(
            select(MemoryEdge).where(
                MemoryEdge.relation == "contradicts",
                or_(
                    col(MemoryEdge.source_memory_id).in_(active_ids),
                    col(MemoryEdge.target_memory_id).in_(active_ids),
                ),
            )
        ).all()

        # 补充缺少的对端节点
        additional_ids = set()
        for e in contra_edges:
            if not self._check_access(owner_id=e.user_id, tenant_id=e.tenant_id):
                continue
            if e.source_memory_id not in active_ids:
                additional_ids.add(e.source_memory_id)
            if e.target_memory_id not in active_ids:
                additional_ids.add(e.target_memory_id)

        for aid in additional_ids:
            item = s.get(MemoryItem, aid)
            if item and self._check_access(owner_id=item.user_id, tenant_id=item.tenant_id):
                permitted_memories.append(item)
                active_ids.add(item.id)

        # 4. 执行交叉勘验
        verified_items = self.cross_verify_memories(memories=permitted_memories, session=s)

        verified_count = sum(1 for it in verified_items if it.verification_status == "verified")
        disputed_count = sum(1 for it in verified_items if it.verification_status == "disputed")

        summary = (
            f"磨勘审计完成：勘验总计 {len(verified_items)} 条，"
            f"交叉证实 {verified_count} 条，存在争议矛盾 {disputed_count} 条。"
        )

        fenced_summary = (
            f'<mokan_audit query="{query}" total="{len(verified_items)}" '
            f'verified="{verified_count}" disputed="{disputed_count}">\n'
            + "\n".join(it.fenced_content for it in verified_items)
            + "\n</mokan_audit>"
        )

        logger.info(
            "磨勘：审计完成（query=%s，总计 %d，证实 %d，争议 %d）",
            query,
            len(verified_items),
            verified_count,
            disputed_count,
        )

        return MokanAuditResult(
            query=query,
            total_audited=len(verified_items),
            verified_count=verified_count,
            disputed_count=disputed_count,
            items=verified_items,
            summary=summary,
            fenced_content=fenced_summary,
        )
