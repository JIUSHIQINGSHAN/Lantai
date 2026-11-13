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
from sqlmodel import Session, col, select

from lantai.core.acl import is_system_viewer, viewer_of
from lantai.core.auth import Principal
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 票 59：扫描候选池硬上界。模块常量而非 Settings 旋钮——这是资源安全上界，
# 不该被运行时配置调大（同 refine_service.REFINE_BATCH_LIMIT_MAX 的处置口径）。
SCAN_CANDIDATES_MAX = 100


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

    # 票 59 死代码处置：`_check_access` 的唯一消费点 `_scan_redundancy` 已把行级
    # ACL 下推 SQL where（None 收敛 viewer_of，同票 57 六引擎口径），Python 侧
    # 第二套判据随之删除——行级 ACL 单一真源即那条 where（钩沉死代码处置同判）。

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
        """扫描当前权限范围内的活跃记忆，查找近义冗余配对。

        扫描口径（票 59）：候选池 = 按 `created_at` 最近优先取 `min(limit,
        SCAN_CANDIDATES_MAX)` 行；池内预分词一次再做两两 set-Jaccard，取最相似
        的 `limit` 对复用 `compare_and_collate` 出报告。`limit` 同时钳制计算池与
        输出（钳制到 [1, SCAN_CANDIDATES_MAX]），`similarity_threshold` 合法区间
        (0, 1]，越界拒绝（ValueError）。早于池窗口的冗余对不可见——宁界不爆，
        资源安全优先于完备性；本工具是 LLM 可自主调用的 MCP 面，计算量必须恒定。
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if isinstance(similarity_threshold, bool) or not isinstance(
            similarity_threshold, (int, float)
        ):
            raise ValueError("similarity_threshold must be a number")
        threshold = float(similarity_threshold)
        if not (0.0 < threshold <= 1.0):
            raise ValueError("similarity_threshold must be in (0, 1]")
        pool_size = max(1, min(limit, SCAN_CANDIDATES_MAX))

        if self.session is not None:
            return self._scan_redundancy(self.session, pool_size, threshold)
        with db.get_session() as s:
            return self._scan_redundancy(s, pool_size, threshold)

    def _scan_redundancy(
        self,
        s: Session,
        pool_size: int,
        similarity_threshold: float,
    ) -> dict[str, Any]:
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        # 行级 ACL 下推 SQL（票 57 口径）：None 收敛 viewer_of="default"（只看默认
        # 属主 + NULL 老行），admin/SYSTEM_VIEWER 显式全表，swarm_shared 公共结晶
        # 人人可读（原 `_check_access` 判序）；租户不匹配同判 False。
        if not (getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal)):
            viewer = viewer_of(self.principal)
            current_tenant = getattr(self.principal, "tenant_id", None)
            owner_clause = (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
            if current_tenant:
                owner_clause = owner_clause & (
                    (MemoryItem.tenant_id.is_(None)) | (MemoryItem.tenant_id == current_tenant)
                )
            stmt = stmt.where(owner_clause | (MemoryItem.shared_scope == "swarm_shared"))

        # 候选池硬上界：最近创建优先（created_at+id 双键稳定序），计算量 O(池²)
        # 恒定，不再全表 O(n²)（票 59 病灶 1+2）。
        stmt = stmt.order_by(col(MemoryItem.created_at).desc(), col(MemoryItem.id).desc()).limit(
            pool_size
        )
        pool = list(s.exec(stmt).all())

        # 预分词一次（O(池) 次 jieba 调用），两两只做廉价 set-Jaccard；报告对
        # （≤ pool_size 个）才复用 compare_and_collate 出完整差异报告——公式同源，
        # 单一真源不复制。
        token_sets = [
            set(w.strip().lower() for w in jieba.cut(m.content or "") if len(w.strip()) > 1)
            for m in pool
        ]
        candidate_pairs: list[tuple[float, int, int]] = []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                union_len = len(token_sets[i].union(token_sets[j]))
                sim = len(token_sets[i].intersection(token_sets[j])) / max(union_len, 1)
                if sim >= similarity_threshold:
                    candidate_pairs.append((sim, i, j))
        candidate_pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

        pairs = [
            self.compare_and_collate(
                text_a=pool[i].content,
                text_b=pool[j].content,
                memory_id_a=pool[i].id,
                memory_id_b=pool[j].id,
            )
            for _, i, j in candidate_pairs[:pool_size]
        ]
        return {
            "total_pairs": len(pairs),
            "redundant_pairs": pairs,
            "scanned_pool": len(pool),
            "candidates_cap": SCAN_CANDIDATES_MAX,
        }
