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
from lantai.gate.relation import classify_relation
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
    common_summary: str = ""
    divergence_tokens: list[str] = field(default_factory=list)
    divergence_tokens_a: list[str] = field(default_factory=list)
    divergence_tokens_b: list[str] = field(default_factory=list)
    divergence_summary: str = ""
    action_suggestion: str = "merge"  # merge | update | retain_both
    correction_suggestion: str = ""
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
            "common_summary": self.common_summary,
            "divergence_tokens": self.divergence_tokens,
            "divergence_tokens_a": self.divergence_tokens_a,
            "divergence_tokens_b": self.divergence_tokens_b,
            "divergence_summary": self.divergence_summary,
            "action_suggestion": self.action_suggestion,
            "correction_suggestion": self.correction_suggestion,
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
        text_a = text_a or ""
        text_b = text_b or ""
        words_a = [w.strip().lower() for w in jieba.cut(text_a) if len(w.strip()) > 1]
        words_b = [w.strip().lower() for w in jieba.cut(text_b) if len(w.strip()) > 1]

        set_a = set(words_a)
        set_b = set(words_b)

        overlap = sorted(set_a.intersection(set_b))
        diff_b = sorted(set_b - set_a)
        diff_a = sorted(set_a - set_b)

        union_len = len(set_a.union(set_b))
        sim = len(overlap) / max(union_len, 1)
        common_summary = f"共同词项: {', '.join(overlap[:8])}" if overlap else "未提取到共同词项"
        relation_ab = classify_relation(text_a, text_b)
        relation_ba = classify_relation(text_b, text_a)

        # 相似度只作标尺；动作以双向结构判类为准，方向不明确时宁可并存。
        if sim < 0.35:
            action = "retain_both"
            summary = "共同词项不足以支持关联，建议分别保留"
            correction = "相似依据不足；请保留两源，不因共享值或地名推断它们是同一事实。"
            proposal = "\n".join(text for text in (text_a, text_b) if text)
        elif relation_ab == relation_ba == "merge":
            action = "merge"
            summary = "双向结构判类一致，建议人工复核后去重"
            correction = "可提炼共同表述；确认来源与归属后再处理，系统不自动删除原记忆。"
            proposal = text_b if len(text_b) >= len(text_a) else text_a
        elif relation_ab == "update" and relation_ba == "merge":
            action = "update"
            summary = "B 侧包含结构化增量，反向校勘未发现替换冲突"
            correction = (
                "结构判类显示来源 B 是信息扩展候选；核验来源与生效时间后再订正，"
                "本建议不覆盖或删除原记忆。"
            )
            proposal = text_b or text_a
        elif relation_ba == "update" and relation_ab == "merge":
            action = "update"
            summary = "A 侧包含结构化增量，反向校勘未发现替换冲突"
            correction = (
                "结构判类显示来源 A 是信息扩展候选；核验来源与生效时间后再订正，"
                "本建议不覆盖或删除原记忆。"
            )
            proposal = text_a or text_b
        else:
            action = "retain_both"
            summary = "双向结构判类未能确认同一事实或唯一更新方向，建议分别保留"
            correction = "目前证据不足以确定订正方向；请保留两源，核实来源与生效时间后再裁决。"
            proposal = "\n".join(text for text in (text_a, text_b) if text)
        divergence_summary = (
            f"{summary}；A 侧特有词项: {', '.join(diff_a[:5]) or '无'}；"
            f"B 侧特有词项: {', '.join(diff_b[:5]) or '无'}"
        )
        relation_score = {"merge": 0.9, "update": 0.75, "retain_both": 0.5}[action]
        # 仅作排序提示，不是经过标注集校准的概率。
        confidence = round(0.5 * sim + 0.5 * relation_score, 4)

        diff = CollationDiff(
            memory_id_a=memory_id_a,
            memory_id_b=memory_id_b,
            text_a=text_a,
            text_b=text_b,
            similarity=sim,
            overlap_tokens=overlap,
            common_summary=common_summary,
            divergence_tokens=diff_b,
            divergence_tokens_a=diff_a,
            divergence_tokens_b=diff_b,
            divergence_summary=divergence_summary,
            action_suggestion=action,
            correction_suggestion=correction,
            synthesized_proposal=proposal,
            confidence=confidence,
            metadata={
                "structural_relation_a_to_b": relation_ab,
                "structural_relation_b_to_a": relation_ba,
                "confidence_method": "heuristic_50_50_lexical_relation",
                "confidence_is_calibrated": False,
            },
        )
        return diff.to_dict(fenced=True)

    def scan_redundancy(
        self,
        limit: int = 50,
        similarity_threshold: float = 0.7,
    ) -> dict[str, Any]:
        """扫描当前权限范围内的活跃记忆，查找近义冗余配对。

        扫描口径（票 59 / ADR-0097）：候选池 = 按 `created_at` 最近优先取
        `min(limit, SCAN_CANDIDATES_MAX)` 行；池内预分词一次再做两两 set-Jaccard，
        用完整阈值候选边识别簇，并只对最相似 `limit` 对生成逐对报告。簇报告统计
        已报告与省略边，未逐对校勘时保守建议待核。`limit` 同时钳制计算池与输出
        （钳制到 [1, SCAN_CANDIDATES_MAX]），`similarity_threshold` 合法区间 (0, 1]，
        越界拒绝（ValueError）。早于池窗口的冗余对不可见——宁界不爆，资源安全优先于
        完备性；本工具是 LLM 可自主调用的 MCP 面，计算量必须恒定。
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
        stmt = select(MemoryItem).where(
            MemoryItem.status == "active",
            col(MemoryItem.lifecycle_status).in_(("active", "weakened")),
        )
        # 行级 ACL 下推 SQL（票 57 口径）：None 收敛 viewer_of="default"（只看默认
        # 属主 + NULL 老行），admin/SYSTEM_VIEWER 显式全表，swarm_shared 公共结晶
        # 人人可读（原 `_check_access` 判序）；租户不匹配同判 False。
        if not (getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal)):
            viewer = viewer_of(self.principal)
            current_tenant = getattr(self.principal, "tenant_id", None)
            owner_clause = (col(MemoryItem.user_id) == viewer) | (col(MemoryItem.user_id).is_(None))
            if current_tenant:
                owner_clause = owner_clause & (
                    (col(MemoryItem.tenant_id).is_(None)) | (MemoryItem.tenant_id == current_tenant)
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
        clusters = self._cluster_candidate_pairs(candidate_pairs, pool, pairs, token_sets)
        return {
            "total_pairs": len(pairs),
            "candidate_pair_count": len(candidate_pairs),
            "redundant_pairs": pairs,
            "total_clusters": len(clusters),
            "redundant_clusters": clusters,
            "scanned_pool": len(pool),
            "candidates_cap": SCAN_CANDIDATES_MAX,
        }

    def _cluster_candidate_pairs(
        self,
        candidate_pairs: list[tuple[float, int, int]],
        pool: list[MemoryItem],
        reported_pairs: list[dict[str, Any]],
        token_sets: list[set[str]],
    ) -> list[dict[str, Any]]:
        """用完整候选图成簇；逐对校勘详情仍受 scan 的 limit 约束。"""
        id_by_index = {index: memory.id for index, memory in enumerate(pool)}
        contents = {memory.id: memory.content or "" for memory in pool}
        tokens_by_id = {id_by_index[index]: tokens for index, tokens in enumerate(token_sets)}
        similarity_by_ids = {
            frozenset((id_by_index[i], id_by_index[j])): similarity
            for similarity, i, j in candidate_pairs
        }
        reported_by_ids = {
            frozenset((pair["memory_id_a"], pair["memory_id_b"])): pair for pair in reported_pairs
        }
        ordered_edges = sorted(
            ((similarity, id_by_index[i], id_by_index[j]) for similarity, i, j in candidate_pairs),
            key=lambda edge: (-edge[0], min(edge[1], edge[2]), max(edge[1], edge[2])),
        )
        groups: list[set[str]] = []

        def cross_edges_exist(left: set[str], right: set[str]) -> bool:
            return all(frozenset((a, b)) in similarity_by_ids for a in left for b in right)

        for _, left, right in ordered_edges:
            candidate = {left, right}
            changed = True
            while changed:
                changed = False
                for group in groups[:]:
                    if candidate <= group:
                        candidate = group
                        break
                    if group <= candidate:
                        groups.remove(group)
                        changed = True
                        break
                    candidate_only = candidate - group
                    group_only = group - candidate
                    if cross_edges_exist(candidate_only, group_only):
                        groups.remove(group)
                        candidate |= group
                        changed = True
                        break
            if candidate not in groups:
                groups.append(candidate)

        groups = [group for group in groups if not any(group < other for other in groups)]

        reports: list[dict[str, Any]] = []
        for members in sorted((g for g in groups if len(g) > 1), key=lambda g: sorted(g)):
            memory_ids = sorted(members)
            member_edges = [
                (frozenset((left, right)), similarity_by_ids[frozenset((left, right))])
                for i, left in enumerate(memory_ids)
                for right in memory_ids[i + 1 :]
            ]
            member_pair_details = [
                reported_by_ids[edge] for edge, _ in member_edges if edge in reported_by_ids
            ]
            representative_edge = min(
                member_edges,
                key=lambda item: (
                    item[0] not in reported_by_ids,
                    -item[1],
                    *sorted(item[0]),
                ),
            )[0]
            representative_pair = reported_by_ids.get(representative_edge)
            if representative_pair is None:
                representative_ids = sorted(representative_edge)
                representative_pair = self.compare_and_collate(
                    contents[representative_ids[0]],
                    contents[representative_ids[1]],
                    memory_id_a=representative_ids[0],
                    memory_id_b=representative_ids[1],
                )
            representative_summary = {
                key: representative_pair[key]
                for key in (
                    "memory_id_a",
                    "memory_id_b",
                    "similarity",
                    "action_suggestion",
                    "correction_suggestion",
                    "divergence_summary",
                )
            }

            pair_count = len(member_edges)
            reported_pair_count = len(member_pair_details)
            omitted_pair_count = pair_count - reported_pair_count
            actions = {
                pair["action_suggestion"]
                for pair in member_pair_details
                if "action_suggestion" in pair
            }
            if representative_pair:
                actions.add(representative_pair["action_suggestion"])
            if omitted_pair_count:
                action = "retain_both"
                correction = (
                    f"簇内另有 {omitted_pair_count} 对尚未逐对校勘；当前仅提供已核配对与代表建议，"
                    "请保留原记录并逐条核验，不按未检查的关系合并。"
                )
            elif "retain_both" in actions:
                action = "retain_both"
                correction = "簇内关系尚未确认；建议保留各条记录，核验来源与生效时间后再裁决。"
            elif "update" in actions:
                action = "update"
                correction = "簇内存在订误差异；请逐条核验来源与生效时间，暂不覆盖或删除原记忆。"
            else:
                action = "merge"
                correction = "簇内配对关系一致；可人工提炼共同表述，核验后再处理原记录。"

            cluster_tokens = [tokens_by_id[memory_id] for memory_id in memory_ids]
            common_tokens = sorted(set.intersection(*cluster_tokens)) if cluster_tokens else []
            all_tokens = set.union(*cluster_tokens) if cluster_tokens else set()
            reports.append(
                {
                    "memory_ids": memory_ids,
                    "member_count": len(memory_ids),
                    "pair_count": pair_count,
                    "reported_pair_count": reported_pair_count,
                    "omitted_pair_count": omitted_pair_count,
                    "similarity_floor": round(min(similarity for _, similarity in member_edges), 4),
                    "suggested_action": action,
                    "correction_suggestion": correction,
                    "common_tokens": common_tokens,
                    "divergence_tokens": sorted(all_tokens - set(common_tokens)),
                    "representative_pair": representative_summary,
                    "pairs": [
                        {
                            "memory_id_a": pair["memory_id_a"],
                            "memory_id_b": pair["memory_id_b"],
                            "similarity": pair["similarity"],
                            "action_suggestion": pair["action_suggestion"],
                            "divergence_summary": pair["divergence_summary"],
                            "divergence_tokens_a": pair["divergence_tokens_a"],
                            "divergence_tokens_b": pair["divergence_tokens_b"],
                        }
                        for pair in sorted(
                            member_pair_details,
                            key=lambda item: (
                                min(item["memory_id_a"], item["memory_id_b"]),
                                max(item["memory_id_a"], item["memory_id_b"]),
                            ),
                        )
                    ],
                }
            )
        return reports
