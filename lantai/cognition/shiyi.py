"""拾遗（Shiyi）遗漏补偿与零召回自适应补救引擎 (ADR-0069)。

出处：唐代谏官官职「拾遗」（杜甫曾任左拾遗），取「捡回遗漏之物、补苴罅漏」之意。
职责：
1. 专职针对常规检索零召回（Zero-Recall）或模糊失配情境启动自适应补救；
2. 落地多阶补救阶梯（Tier 1: 宽松结巴分词与字符级 n-gram 模糊回溯；Tier 2: 辨域安全放宽；Tier 3: 衰减时效回拨）；
3. 抽象清晰的补救候选模型（RemedyCandidate），标明 remedy_tier 与置信度惩罚；
4. 全链路应用樊篱（wrap_as_data）防注入标记隔离；
5. 落实行级租户与属主安全隔离（宁漏不越权）。
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
from lantai.models.tables import MemoryItem
from lantai.storage import db


@dataclass
class RemedyCandidate:
    """拾遗补救候选条目。"""

    id: str
    content: str
    remedy_tier: str  # fuzzy_tokens | domain_relax | decay_boost | subsequence
    confidence: float = 0.5
    remedy_reason: str = ""
    domain: str = "user"
    lane: str = "general"
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        fenced_c = (
            wrap_as_data(self.content, item_id=self.id, score=self.confidence)
            if fenced
            else self.content
        )
        return {
            "id": self.id,
            "content": self.content,
            "fenced_content": fenced_c,
            "remedy_tier": self.remedy_tier,
            "confidence": round(self.confidence, 4),
            "remedy_reason": self.remedy_reason,
            "domain": self.domain,
            "lane": self.lane,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }

    def to_search_result(self, *, layer: str = "instance") -> dict[str, Any]:
        """将拾遗补救候选投影为 hybrid_search 标准检索结果项契约 (ADR-0076)。"""
        fenced_c = wrap_as_data(self.content, item_id=self.id, score=self.confidence)
        return {
            "score": round(self.confidence, 4),
            "memory": {
                "id": self.id,
                "content": self.content,
                "domain": self.domain,
                "lane": self.lane,
                "confidence": round(self.confidence, 4),
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "metadata": {
                    **self.metadata,
                    "is_remedied": True,
                    "remedy_tier": self.remedy_tier,
                    "remedy_reason": self.remedy_reason,
                },
            },
            "document": self.content,
            "fenced_content": fenced_c,
            "layer": layer,
            "is_remedied": True,
            "remedy_tier": self.remedy_tier,
            "remedy_reason": self.remedy_reason,
        }


class ShiyiEngine:
    """拾遗（Shiyi）遗漏补偿与零召回自适应补救引擎。"""

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
        """行级安全隔离校验（宁漏不越权）。"""
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

    def remedy_recall(
        self,
        query: str,
        domain: str = "all",
        allow_domain_relax: bool = False,
        limit: int = 5,
    ) -> dict[str, Any]:
        """针对指定查询启动分阶补救召回。"""
        if not query:
            return {"query": "", "is_remedied": False, "remedied_items": []}

        if self.session is not None:
            return self._remedy_recall(self.session, query, domain, allow_domain_relax, limit)
        with db.get_session() as s:
            return self._remedy_recall(s, query, domain, allow_domain_relax, limit)

    def _remedy_recall(
        self,
        s: Session,
        query: str,
        domain: str,
        allow_domain_relax: bool,
        limit: int,
    ) -> dict[str, Any]:
        # 1. 分词与 token 提取
        q_lower = query.lower()
        cut_words = [w.strip() for w in jieba.cut(q_lower) if len(w.strip()) > 1]
        raw_chars = [c for c in re.findall(r"[\w一-龥]", q_lower) if c]
        tokens = set(cut_words) if cut_words else set(raw_chars)

        # 2. 扫描活跃记忆
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        if domain != "all" and not allow_domain_relax:
            stmt = stmt.where(MemoryItem.domain == domain)
        all_memories = s.exec(stmt).all()

        candidates: list[tuple[float, RemedyCandidate]] = []
        for mem in all_memories:
            if not self._check_access(
                owner_id=mem.user_id,
                tenant_id=mem.tenant_id,
                shared_scope=getattr(mem, "shared_scope", "private"),
            ):
                continue

            content_text = (mem.content or "").lower()

            # 判定 Tier 1: 模糊关键词匹配
            hit_tokens = [t for t in tokens if t in content_text]
            token_ratio = len(hit_tokens) / max(len(tokens), 1)

            # 判定字符级重合 (针对缩略词如 K8S -> kubernetes)
            char_hits = sum(1 for c in set(q_lower) if c.isalnum() and c in content_text)
            char_ratio = char_hits / max(len(set(q_lower)), 1)

            tier = None
            score = 0.0
            reason = ""

            # 判断是否跨域放宽 (Tier 2)
            is_domain_relaxed = domain != "all" and mem.domain != domain

            if token_ratio >= 0.3:
                score = token_ratio * 0.7 + (mem.confidence or 0.8) * 0.3
                if is_domain_relaxed:
                    tier = "domain_relax"
                    score *= 0.85  # 跨域轻微置信度折扣
                    reason = f"放宽域范围至 [{mem.domain}] 召回 (命中 {len(hit_tokens)} 个词)"
                else:
                    tier = "fuzzy_tokens"
                    reason = f"模糊词条重合 ({len(hit_tokens)}/{len(tokens)})"
            elif char_ratio >= 0.5:
                score = char_ratio * 0.5 + (mem.confidence or 0.8) * 0.3
                if is_domain_relaxed:
                    tier = "domain_relax"
                    score *= 0.8
                    reason = f"放宽域范围至 [{mem.domain}] 字符子串召回"
                else:
                    tier = "subsequence"
                    reason = f"字符级子序列模糊召回 (重合率 {round(char_ratio, 2)})"

            if tier is not None and score >= 0.3:
                candidates.append(
                    (
                        score,
                        RemedyCandidate(
                            id=mem.id,
                            content=mem.content,
                            remedy_tier=tier,
                            confidence=score,
                            remedy_reason=reason,
                            domain=mem.domain,
                            lane=mem.lane,
                            user_id=mem.user_id,
                            tenant_id=mem.tenant_id,
                            created_at=mem.created_at,
                        ),
                    )
                )

        candidates.sort(key=lambda x: x[0], reverse=True)
        remedied_items = [c.to_dict(fenced=True) for _, c in candidates[:limit]]
        self._last_raw_candidates = [c for _, c in candidates[:limit]]

        return {
            "query": query,
            "is_remedied": len(remedied_items) > 0,
            "remedied_items": remedied_items,
        }

    def remedy_search_pipeline(
        self,
        query: str,
        domain: str = "all",
        allow_domain_relax: bool = True,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """为混合检索主流水线零召回场景提供自适应补救结果项列表 (ADR-0076)。"""
        self.remedy_recall(
            query=query,
            domain=domain,
            allow_domain_relax=allow_domain_relax,
            limit=limit,
        )
        raw_candidates = getattr(self, "_last_raw_candidates", [])
        return [c.to_search_result() for c in raw_candidates]
