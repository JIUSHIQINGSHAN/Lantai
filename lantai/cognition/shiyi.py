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
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jieba
from sqlalchemy import or_
from sqlmodel import Session, col, select

from lantai.core.acl import active_bindings, allowed_lanes, viewer_of
from lantai.core.auth import Principal
from lantai.core.settings import settings
from lantai.core.text import LIKE_ESCAPE_CHAR, like_escape
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import LifecycleStatus, MemoryItem
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
                "created_at": iso_utc(self.created_at) or None,
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
        # N-02 熔断时钟（v0.63 票 02）：扫描预算基于单调时钟判定。
        # 抽成实例属性的唯一目的：测试用 monkeypatch 注入假时钟推进，
        # 绝不 time.sleep 真实等待（CI 抖动 = 假红/假绿，测试纪律红线）。
        self._now = time.perf_counter

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
        federation_id: str | None = None,
        shared_scope: str = "private",
        lane: str | None = None,
    ) -> bool:
        """行级安全隔离校验（宁漏不越权）。

        引擎层保持既有契约：`principal=None` 全放——worker/CLI/直调的内部
        调用方要靠它不空转（`tests/test_key_gate_and_shiyi_perf.py` 用无身份
        直调断言能看见任意属主的行，是钉住的行为）。**匿名外部面的收敛在调用
        方**：`services/shiyi_service.py` 的入口把 None 换成 default 属主，
        与 `hybrid_search._converge_principal` 同一范式（不动承重墙）。
        """
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        lanes = getattr(self.principal, "allowed_lanes", None)
        agent_id = getattr(self.principal, "agent_id", None)
        if agent_id and active_bindings():
            lanes = allowed_lanes(agent_id) or []
        if lanes is not None and lane not in lanes:
            return False
        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)
        current_federation = getattr(self.principal, "federation_id", None)

        if tenant_id and tenant_id != current_tenant:
            return False
        if federation_id and federation_id != current_federation:
            return False
        if shared_scope == "swarm_shared" and tenant_id and tenant_id == current_tenant:
            return True

        return not (owner_id is not None and owner_id != viewer)

    def remedy_recall(
        self,
        query: str,
        domain: str = "all",
        allow_domain_relax: bool = False,
        limit: int = 5,
    ) -> dict[str, Any]:
        """针对指定查询启动分阶补救召回。"""
        self._last_raw_candidates: list[RemedyCandidate] = []
        if not query.strip() or limit <= 0 or not re.search(r"\w", query):
            return {
                "query": query,
                "is_remedied": False,
                "remedied_items": [],
                "scanned_count": 0,
                "scan_truncated": False,
            }

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
        # 词项、三元组和进入 Python 的行数均设硬上限；各阶共用一个扫描预算。
        q_lower = unicodedata.normalize("NFKC", query).casefold()
        cut_words = [w.strip() for w in jieba.cut(q_lower) if len(w.strip()) > 1]
        raw_chars = [c for c in re.findall(r"[\w一-龥]", q_lower) if c]
        tokens = set(dict.fromkeys(cut_words or raw_chars))
        tokens = set(sorted(tokens, key=lambda t: (q_lower.find(t), t))[:32])
        grams = set(
            list(
                dict.fromkeys(
                    word[i : i + 3]
                    for word in re.findall(r"\w+", q_lower)
                    for i in range(len(word) - 2)
                )
            )[:64]
        )
        current_time = utcnow()
        base = select(MemoryItem).where(
            MemoryItem.status == "active",
            col(MemoryItem.lifecycle_status).not_in(
                [LifecycleStatus.SUPERSEDED, LifecycleStatus.RETIRED]
            ),
            or_(col(MemoryItem.valid_from).is_(None), col(MemoryItem.valid_from) <= current_time),
            or_(col(MemoryItem.valid_to).is_(None), col(MemoryItem.valid_to) > current_time),
        )
        # 直连补救与主路同样排除逐字原文，回拨不能恢复已失效的主张。
        if not settings.VERBATIM_IN_RECALL:
            base = base.where(MemoryItem.memory_type != "verbatim")
        scopes = [base] if domain == "all" else [base.where(MemoryItem.domain == domain)]
        if domain != "all" and allow_domain_relax:
            scopes.append(base.where(MemoryItem.domain != domain))
        budget_s = max(settings.SHIYI_SCAN_BUDGET_MS, 0) / 1000.0
        t_start = self._now()
        scan_truncated = False
        seen: set[str] = set()
        candidates: list[tuple[int, float, RemedyCandidate]] = []
        regular_count = 0
        for scope in scopes:
            if regular_count >= limit or scan_truncated:
                break
            for terms in (tokens, grams):
                if not terms or regular_count >= limit or scan_truncated:
                    break
                if self._now() - t_start > budget_s:
                    scan_truncated = True
                    break
                stmt = scope.where(
                    or_(
                        *[
                            col(MemoryItem.content).like(
                                f"%{like_escape(t)}%", escape=LIKE_ESCAPE_CHAR
                            )
                            for t in sorted(terms)
                        ]
                    )
                )
                if seen:
                    stmt = stmt.where(col(MemoryItem.id).not_in(sorted(seen)))
                # 多取一行仅用于准确报告截断；不全量物化，也不在每阶重置配额。
                rows = s.exec(
                    stmt.order_by(MemoryItem.id)
                    .limit(513 - len(seen))
                    .execution_options(yield_per=64)
                )
                try:
                    for mem in rows:
                        if len(seen) >= 512 or self._now() - t_start > budget_s:
                            scan_truncated = True
                            break
                        seen.add(mem.id)
                        if not self._check_access(
                            owner_id=mem.user_id,
                            tenant_id=mem.tenant_id,
                            federation_id=getattr(mem, "federation_id", None),
                            shared_scope=mem.shared_scope,
                            lane=mem.lane,
                        ):
                            continue
                        content = unicodedata.normalize("NFKC", mem.content or "").casefold()
                        token_hits = sum(t in content for t in tokens)
                        token_ratio = token_hits / max(len(tokens), 1)
                        gram_hits = sum(g in content for g in grams)
                        gram_ratio = gram_hits / max(len(grams), 1)
                        confidence = min(
                            max(mem.confidence if mem.confidence is not None else 0.8, 0.0), 1.0
                        )
                        if token_ratio >= 0.3:
                            tier = "fuzzy_tokens"
                            lexical_score = token_ratio * 0.7 + confidence * 0.3
                            reason = f"模糊词条重合 ({token_hits}/{len(tokens)})"
                        elif gram_hits >= 2 and gram_ratio >= 0.6:
                            tier = "subsequence"
                            lexical_score = gram_ratio * 0.5 + confidence * 0.3
                            reason = f"连续字符三元组重合 ({gram_hits}/{len(grams)})"
                        else:
                            continue
                        relaxed = domain != "all" and mem.domain != domain
                        priority = 1 if relaxed else 0
                        if relaxed:
                            lexical_score *= 0.85
                            tier = "domain_relax"
                            reason = f"放宽域范围至 [{mem.domain}]；{reason}"
                        decay = min(max(mem.decay_score, 0.0), 1.0)
                        score = lexical_score * decay
                        before = score
                        if score < 0.3:
                            # 只回拨衰减惩罚的一半；不改主张有效期、存储分数与生命周期。
                            score = lexical_score * (0.5 + 0.5 * decay)
                            tier = "decay_boost"
                            priority = 2
                            reason += "；衰减惩罚减半回拨"
                        if score < 0.3:
                            continue
                        if priority < 2:
                            regular_count += 1
                        candidates.append(
                            (
                                priority,
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
                                    metadata={
                                        "original_query": query,
                                        "domain_relaxed": relaxed,
                                        "decay_score": decay,
                                        "score_before": round(before, 4),
                                        "score_after": round(score, 4),
                                    },
                                ),
                            )
                        )
                finally:
                    rows.close()

        candidates.sort(key=lambda x: (x[0], -x[1], x[2].id))
        self._last_raw_candidates = [c for _, _, c in candidates[:limit]]
        remedied_items = [c.to_dict(fenced=True) for c in self._last_raw_candidates]

        return {
            "query": query,
            "is_remedied": len(remedied_items) > 0,
            "remedied_items": remedied_items,
            "scanned_count": len(seen),
            "scan_truncated": scan_truncated,
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
