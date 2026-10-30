"""校雠（Jiaochou）智能语义去重与知识对齐订误引擎 (ADR-0070)。

出处：西汉刘向《别录》「一人读书，校其上下，得谬误而差正之，谓之校；一人持本，一人读书，若冤家相对，谓之雠」。
职责：
1. 双源事实语义校勘（compare_and_collate）：逐句对齐、提取共有实词与增量分歧点；
2. 给出结构化订误建议（CollationDiff: merge / update / retain_both）；
3. 存量活跃记忆冗余扫描（scan_redundancy）：发现相近记忆簇并提供合并提纯建议；
4. 全链路应用樊篱（wrap_as_data）防注入标记隔离；
5. 落实行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jieba
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.storage import db


@dataclass
class CollationDiff:
    """校雠对齐校勘差异模型。"""

    memory_id_a: str | None
    memory_id_b: str | None
    text_a: str
    text_b: str
    similarity: float
    overlap_tokens: list[str] = field(default_factory=list)
    divergence_tokens: list[str] = field(default_factory=list)
    divergence_summary: str = ""
    action_suggestion: str = "merge"  # merge | update | retain_both
    synthesized_proposal: str = ""
    confidence: float = 0.8
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        proposal = self.synthesized_proposal or self.text_b or self.text_a
        fenced_p = (
            wrap_as_data(
                proposal, item_id=self.memory_id_b or self.memory_id_a, score=self.confidence
            )
            if fenced
            else proposal
        )
        return {
            "memory_id_a": self.memory_id_a,
            "memory_id_b": self.memory_id_b,
            "text_a": self.text_a,
            "text_b": self.text_b,
            "similarity": round(self.similarity, 4),
            "overlap_tokens": self.overlap_tokens,
            "divergence_tokens": self.divergence_tokens,
            "divergence_summary": self.divergence_summary,
            "action_suggestion": self.action_suggestion,
            "synthesized_proposal": proposal,
            "fenced_proposal": fenced_p,
            "confidence": round(self.confidence, 4),
            "metadata": self.metadata,
        }


class JiaochouEngine:
    """校雠（Jiaochou）智能语义去重与知识对齐订误引擎。"""

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

    def compare_and_collate(
        self,
        text_a: str,
        text_b: str,
        memory_id_a: str | None = None,
        memory_id_b: str | None = None,
    ) -> dict[str, Any]:
        """对两段文本/记忆执行双向语义校勘对齐。"""
        words_a = [w.strip().lower() for w in jieba.cut(text_a or "") if len(w.strip()) > 1]
        words_b = [w.strip().lower() for w in jieba.cut(text_b or "") if len(w.strip()) > 1]

        set_a = set(words_a)
        set_b = set(words_b)

        overlap = list(set_a.intersection(set_b))
        diff_b = list(set_b - set_a)
        diff_a = list(set_a - set_b)

        union_len = len(set_a.union(set_b))
        sim = len(overlap) / max(union_len, 1)

        # 判定动作建议
        if sim >= 0.75:
            action = "merge"
            summary = "表述高度同义，建议直接合并去重"
            proposal = text_b if len(text_b) >= len(text_a) else text_a
        elif sim >= 0.35:
            action = "update"
            divergence_items = diff_b or diff_a
            summary = f"存在增量细节与差异点 ({', '.join(divergence_items[:3])})"
            # 融合建议
            proposal = f"{text_a}；补充: {', '.join(diff_b)}" if diff_b else text_b
        else:
            action = "retain_both"
            summary = "差异较大，判定为独立事实，建议分别保留"
            proposal = f"{text_a}\n{text_b}"

        diff = CollationDiff(
            memory_id_a=memory_id_a,
            memory_id_b=memory_id_b,
            text_a=text_a,
            text_b=text_b,
            similarity=sim,
            overlap_tokens=overlap,
            divergence_tokens=diff_b,
            divergence_summary=summary,
            action_suggestion=action,
            synthesized_proposal=proposal,
            confidence=0.85,
        )
        return diff.to_dict(fenced=True)

    def scan_redundancy(
        self,
        limit: int = 50,
        similarity_threshold: float = 0.7,
    ) -> dict[str, Any]:
        """扫描当前权限范围内的活跃记忆，查找近义冗余配对。"""
        if self.session is not None:
            return self._scan_redundancy(self.session, limit, similarity_threshold)
        with db.get_session() as s:
            return self._scan_redundancy(s, limit, similarity_threshold)

    def _scan_redundancy(
        self,
        s: Session,
        limit: int,
        similarity_threshold: float,
    ) -> dict[str, Any]:
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        mems = s.exec(stmt).all()

        accessible_mems = [
            m
            for m in mems
            if self._check_access(
                owner_id=m.user_id,
                tenant_id=m.tenant_id,
                shared_scope=getattr(m, "shared_scope", "private"),
            )
        ]

        pairs: list[dict[str, Any]] = []
        n = len(accessible_mems)
        for i in range(n):
            for j in range(i + 1, n):
                m_a = accessible_mems[i]
                m_b = accessible_mems[j]

                # 快速字符与词条初筛
                diff = self.compare_and_collate(
                    text_a=m_a.content,
                    text_b=m_b.content,
                    memory_id_a=m_a.id,
                    memory_id_b=m_b.id,
                )
                if diff["similarity"] >= similarity_threshold:
                    pairs.append(diff)
                    if len(pairs) >= limit:
                        break
            if len(pairs) >= limit:
                break

        pairs.sort(key=lambda x: x["similarity"], reverse=True)
        return {
            "total_pairs": len(pairs),
            "redundant_pairs": pairs,
        }
