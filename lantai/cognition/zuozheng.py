"""佐证（Zuozheng）证据链反查与可解释性溯源图谱引擎 (ADR-0068)。

出处：《宋刑统》「案情显明，须得佐证」；喻指辅助证据、相互勘验印证与决策依据解释。
职责：
1. 抽象多维证据节点（EvidenceNode）与证据拓扑链（ProofChain）；
2. 围绕目标记忆展开 MemoryEdge 因果推演边（causes / supports / refines 等）与关联 Evidence 实体；
3. 支持基于自然语言断言的佐证动态召回与置信度量化评定；
4. 全链路应用樊篱（wrap_as_data）防注入标记隔离；
5. 落实行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jieba
from sqlalchemy import or_
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import Evidence, MemoryEdge, MemoryItem
from lantai.storage import db


@dataclass
class EvidenceNode:
    """佐证证据图谱节点。"""

    id: str
    content: str
    node_type: str  # memory | evidence | raw_document
    confidence: float = 1.0
    support_direction: str = "supports"  # supports | causes | refines | direct_evidence
    relation_reason: str = ""
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        """序列化输出，支持樊篱安全包裹。"""
        fenced_c = (
            wrap_as_data(self.content, item_id=self.id, score=self.confidence)
            if fenced
            else self.content
        )
        return {
            "id": self.id,
            "content": self.content,
            "fenced_content": fenced_c,
            "node_type": self.node_type,
            "confidence": round(self.confidence, 4),
            "support_direction": self.support_direction,
            "relation_reason": self.relation_reason,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


@dataclass
class ProofChain:
    """证据链与反查结论。"""

    target_memory_id: str | None
    target_content: str
    evidence_nodes: list[EvidenceNode] = field(default_factory=list)
    overall_confidence: float = 0.0
    fenced_target_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_memory_id": self.target_memory_id,
            "target_content": self.target_content,
            "fenced_target_content": self.fenced_target_content
            or wrap_as_data(self.target_content, item_id=self.target_memory_id),
            "confidence_score": round(self.overall_confidence, 4),
            "total_nodes": len(self.evidence_nodes),
            "evidence_nodes": [node.to_dict(fenced=True) for node in self.evidence_nodes],
        }


class ZuozhengEngine:
    """佐证（Zuozheng）证据链反查与可解释性溯源图谱引擎。"""

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

    def inspect_evidence(self, memory_id: str, max_depth: int = 2) -> dict[str, Any]:
        """针对指定 memory_id，反向追踪其证据支撑拓扑图谱。"""
        if not memory_id:
            return ProofChain(target_memory_id=None, target_content="").to_dict()

        if self.session is not None:
            return self._inspect_evidence(self.session, memory_id, max_depth)
        with db.get_session() as s:
            return self._inspect_evidence(s, memory_id, max_depth)

    def _inspect_evidence(self, s: Session, memory_id: str, max_depth: int) -> dict[str, Any]:
        target = s.get(MemoryItem, memory_id)
        if not target or target.status != "active":
            return ProofChain(target_memory_id=None, target_content="").to_dict()

        if not self._check_access(
            owner_id=target.user_id,
            tenant_id=target.tenant_id,
            shared_scope=getattr(target, "shared_scope", "private"),
        ):
            return ProofChain(target_memory_id=None, target_content="").to_dict()

        evidence_nodes: list[EvidenceNode] = []
        visited_ids = {memory_id}

        # 1. 查询直接挂在该记忆下的 Evidence 实体
        evs = s.exec(
            select(Evidence).where(
                Evidence.source_memory_id == memory_id, Evidence.status == "active"
            )
        ).all()
        for ev in evs:
            if not self._check_access(owner_id=ev.user_id, tenant_id=ev.tenant_id):
                continue
            evidence_nodes.append(
                EvidenceNode(
                    id=ev.id,
                    content=ev.content,
                    node_type="evidence",
                    confidence=ev.reliability,
                    support_direction="direct_evidence",
                    relation_reason=f"直接证据（类型: {ev.evidence_type}）",
                    user_id=ev.user_id,
                    tenant_id=ev.tenant_id,
                    created_at=ev.created_at,
                    metadata={"evidence_type": ev.evidence_type},
                )
            )

        # 2. 遍历 MemoryEdge 展开入度边（source_memory_id 指向当前 memory_id）
        frontier = [memory_id]
        current_depth = 0
        while frontier and current_depth < max_depth:
            next_frontier = []
            for target_id in frontier:
                edges = s.exec(
                    select(MemoryEdge).where(
                        MemoryEdge.target_memory_id == target_id,
                        MemoryEdge.relation.in_(
                            ("supports", "causes", "refines", "triggered_by", "leads_to")
                        ),
                    )
                ).all()
                for edge in edges:
                    if not self._check_access(owner_id=edge.user_id, tenant_id=edge.tenant_id):
                        continue
                    src_id = edge.source_memory_id
                    if src_id in visited_ids:
                        continue
                    visited_ids.add(src_id)

                    src_mem = s.get(MemoryItem, src_id)
                    if not src_mem or src_mem.status != "active":
                        continue
                    if not self._check_access(
                        owner_id=src_mem.user_id,
                        tenant_id=src_mem.tenant_id,
                        shared_scope=getattr(src_mem, "shared_scope", "private"),
                    ):
                        continue

                    direction = edge.relation
                    evidence_nodes.append(
                        EvidenceNode(
                            id=src_mem.id,
                            content=src_mem.content,
                            node_type="memory",
                            confidence=min(edge.confidence, src_mem.confidence or 0.8),
                            support_direction=direction,
                            relation_reason=edge.reason or f"关联关系: {direction}",
                            user_id=src_mem.user_id,
                            tenant_id=src_mem.tenant_id,
                            created_at=src_mem.created_at,
                            metadata={"edge_id": edge.id, "relation": edge.relation},
                        )
                    )
                    next_frontier.append(src_id)
            frontier = next_frontier
            current_depth += 1

        # 计算综合置信度
        scores = [n.confidence for n in evidence_nodes]
        base_confidence = target.confidence if target.confidence is not None else 0.8
        if scores:
            overall_confidence = max(base_confidence, sum(scores) / len(scores))
        else:
            overall_confidence = base_confidence

        chain = ProofChain(
            target_memory_id=target.id,
            target_content=target.content,
            fenced_target_content=wrap_as_data(target.content, item_id=target.id),
            evidence_nodes=evidence_nodes,
            overall_confidence=overall_confidence,
        )
        return chain.to_dict()

    def verify_claim(self, claim: str, limit: int = 5) -> dict[str, Any]:
        """根据自然语言断言，检索匹配支撑的佐证节点并计算置信度。"""
        if not claim:
            return {
                "claim": "",
                "supporting_nodes": [],
                "verified": False,
                "overall_confidence": 0.0,
            }

        if self.session is not None:
            return self._verify_claim(self.session, claim, limit)
        with db.get_session() as s:
            return self._verify_claim(s, claim, limit)

    def _verify_claim(self, s: Session, claim: str, limit: int) -> dict[str, Any]:
        # 中文分词与实词提取
        cut_words = [w.strip().lower() for w in jieba.cut(claim) if len(w.strip()) > 1]
        tokens = set(cut_words) if cut_words else set(re.findall(r"[\w一-龥]+", claim.lower()))
        meaningful_tokens = {t for t in tokens if len(t) > 1} or tokens

        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        all_memories = s.exec(stmt).all()

        scored_nodes: list[tuple[float, EvidenceNode]] = []
        for mem in all_memories:
            if not self._check_access(
                owner_id=mem.user_id,
                tenant_id=mem.tenant_id,
                shared_scope=getattr(mem, "shared_scope", "private"),
            ):
                continue

            content_text = (mem.content or "").lower()
            hit_count = sum(1 for token in meaningful_tokens if token in content_text)
            if hit_count == 0:
                continue

            # 匹配分数 = 词条重合比例 * 记忆自身置信度
            match_ratio = hit_count / max(len(meaningful_tokens), 1)
            mem_conf = mem.confidence if mem.confidence is not None else 0.8
            final_score = match_ratio * 0.6 + mem_conf * 0.4

            node = EvidenceNode(
                id=mem.id,
                content=mem.content,
                node_type="memory",
                confidence=final_score,
                support_direction="supports",
                relation_reason=f"断言关键词命中 ({hit_count}/{len(meaningful_tokens)})",
                user_id=mem.user_id,
                tenant_id=mem.tenant_id,
                created_at=mem.created_at,
            )
            scored_nodes.append((final_score, node))

        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        top_nodes = [node for _, node in scored_nodes[:limit]]

        verified = False
        overall_conf = 0.0
        if top_nodes:
            overall_conf = sum(n.confidence for n in top_nodes) / len(top_nodes)
            verified = overall_conf >= 0.5

        return {
            "claim": claim,
            "supporting_nodes": [n.to_dict(fenced=True) for n in top_nodes],
            "verified": verified,
            "overall_confidence": round(overall_conf, 4),
        }
