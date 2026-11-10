"""参商（Canshang）深度矛盾冲突检测与信念一致性仲裁引擎 (ADR-0062)

典籍出处：《左传·昭公元年》「虽相背而不同出，如参商之不可见也。」
解决问题：
1. 两两文本主张相反与属性互斥冲突检测；
2. 跨因果边（MemoryEdge: causes/leads_to）的多跳隐性矛盾推导（Multi-hop Causal Contradiction）；
3. 全域/指定实体信念一致性审计矩阵扫描（Consistency Audit Matrix）；
4. 基于更漏双时间轴（event_time）、证据链与反事实必要性的多方深度仲裁直断（Deep Arbitration）。

遵循 ADR-0001 门面铁律、租户行级安全隔离与「宁 miss 不脏写」纪律。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from lantai.cognition.conflicts import ConflictEngine, _temporal_recency
from lantai.core.auth import Principal
from lantai.core.logger import logger
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage import db


def _content_tokens(text: str) -> set[str]:
    """票 68：词级实词集合（jieba，len>1 过滤单字虚词）——佐证 verify_claim
    与票 65 _relevant_tokens 同源口径。替换单字字符集交叉判主题相关（假矛盾源：
    「系统管理员启用…」与「下班后管理员关闭…」共享 3 个高频单字即触发）。"""
    import jieba

    return {w.strip() for w in jieba.cut(text) if len(w.strip()) > 1}


@dataclass
class ConsistencyViolation:
    """参商冲突违例。"""

    conflicting_memory_ids: list[str]
    violation_type: (
        str  # direct_contradiction | attribute_conflict | multihop_causal | temporal_inconsistency
    )
    severity: str  # low | medium | high
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsistencyAuditReport:
    """全域/实体信念一致性审计报告。"""

    total_scanned: int
    violation_count: int
    violations: list[ConsistencyViolation]
    audited_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    entity_name: str | None = None


@dataclass
class DeepArbitrationResult:
    """深度多方仲裁直断结果。"""

    dominant_memory_id: str | None
    subordinate_memory_ids: list[str]
    resolution_strategy: str  # supersede | deprecate_subordinate | win_late | hold_unresolved
    decision_trace: dict[str, Any]
    narrative: str


# 否定词/互斥语义前缀对（无外部大模型依赖下的确定性语义规则）
OPPOSITE_PAIRS = [
    ("启用", "关闭"),
    ("开启", "禁用"),
    ("开启", "关闭"),
    ("打开", "关闭"),
    ("允许", "禁止"),
    ("支持", "不支持"),
    ("通过", "拒绝"),
    ("成功", "失败"),
    ("启用", "废除"),
    ("单机", "集群"),
    ("单节点", "多节点"),
    ("enabled", "disabled"),
    ("on", "off"),
    ("active", "inactive"),
    ("true", "false"),
    ("yes", "no"),
]

# 票 68：名词性状态反义子集。这两对的两词本身就是状态主张的本体（部署拓扑/
# 节点形态），同现即同一对象的相斥状态断言——真矛盾（多主集群 vs 单机独立）
# 两句间可以零实词交叉，主题交叉闸不适用。其余动词性开关对（启用/关闭作用于
# 各自宾语）与否定衍生对（支持/不支持、active/inactive）从严：须词级主题
# 交叉 ≥3 或同实体才判矛盾（否则「管理员启用备份」vs「管理员关闭照明」
# 成假矛盾，红测锁）。
_STATE_ANTONYM_PAIRS = {
    frozenset(("单机", "集群")),
    frozenset(("单节点", "多节点")),
}


class CanshangEngine:
    """参商·深度冲突检测与信念一致性仲裁引擎。"""

    def __init__(
        self,
        session: Session | None = None,
        principal: Principal | None = None,
    ):
        self.session = session
        self.principal = principal
        self._conflict_engine = ConflictEngine()

    def _get_session(self) -> Session:
        if self.session is not None:
            return self.session
        return db.get_session()  # 票 67 弃用：仅存量调用兼容，公开方法已拆双轨

    def _check_access(self, item: MemoryItem) -> bool:
        """行级租户与属主 ACL 隔离校验。"""
        if self.principal is None:
            return True
        if self.principal.is_admin:
            return True
        if item.user_id and self.principal.user_id and item.user_id != self.principal.user_id:
            return False
        return not (
            self.principal.allowed_lanes
            and item.lane
            and item.lane not in self.principal.allowed_lanes
        )

    def detect_pairwise_contradiction(
        self,
        item_a: MemoryItem,
        item_b: MemoryItem,
        session: Session | None = None,
    ) -> ConsistencyViolation | None:
        """检测两记忆之间是否存在直接主张冲突或属性互斥。"""
        if not self._check_access(item_a) or not self._check_access(item_b):
            return None

        st_a = item_a.structure if isinstance(item_a.structure, dict) else {}
        st_b = item_b.structure if isinstance(item_b.structure, dict) else {}

        # 1. 结构化属性互斥检测 (如 entity 相同但 value/role/state/policy 互斥)
        ent_a = st_a.get("entity")
        ent_b = st_b.get("entity")

        is_same_entity = (ent_a and ent_b and ent_a == ent_b) or False

        # 检查结构化字段中的显式互斥
        # 若双方都有明确的 entity 且不同，则互斥仅发生在有交叉特征/策略且无独立实体的场景
        for key in ("value", "feature", "role", "state", "policy", "action"):
            val_a = str(st_a.get(key, ""))
            val_b = str(st_b.get(key, ""))
            if val_a and val_b and val_a != val_b:
                # 实体约束：若两方都声明了 entity 且不相同，不属于同一实体的状态冲突
                if ent_a and ent_b and ent_a != ent_b:
                    continue

                for w1, w2 in OPPOSITE_PAIRS:
                    if (w1 in val_a and w2 in val_b) or (w2 in val_a and w1 in val_b):
                        field_desc = (
                            f"{key} ({st_a.get('feature', '')})" if st_a.get("feature") else key
                        )
                        return ConsistencyViolation(
                            conflicting_memory_ids=[item_a.id, item_b.id],
                            violation_type="attribute_conflict",
                            severity="high",
                            description=f"实体或维度 '{field_desc}' 存在互斥状态：'{val_a}' vs '{val_b}'（参商抵触/矛盾）",
                            metadata={"field": key, "val_a": val_a, "val_b": val_b},
                        )
                # 角色互斥 (如 PM vs Dev 且明确互斥)
                if key == "role" and is_same_entity:
                    return ConsistencyViolation(
                        conflicting_memory_ids=[item_a.id, item_b.id],
                        violation_type="attribute_conflict",
                        severity="medium",
                        description=f"实体 '{ent_a}' 存在冲突的身份主张：'{val_a}' vs '{val_b}'",
                        metadata={"field": key, "val_a": val_a, "val_b": val_b},
                    )

        # 2. 文本正文互斥词对检测
        content_a = item_a.content or ""
        content_b = item_b.content or ""

        # 文本中是否涉及共同关键词但结论相斥
        for w1, w2 in OPPOSITE_PAIRS:
            if (w1 in content_a and w2 in content_b) or (w2 in content_a and w1 in content_b):
                # 票 68：两档判定。名词性状态反义（单机/集群）同现即判——两词
                # 即状态本体，真矛盾可以零实词交叉。其余对（动词性开关/否定
                # 衍生）改词级实词交叉——原单字字符集 `set(content)` 对自然
                # 语言几乎恒 ≥3（的/是/在级高频字），互斥词对只要各自出现一次
                # 就判「直接矛盾」，无关主题成片误报（假矛盾病灶）。
                if frozenset((w1, w2)) in _STATE_ANTONYM_PAIRS:
                    return ConsistencyViolation(
                        conflicting_memory_ids=[item_a.id, item_b.id],
                        violation_type="direct_contradiction",
                        severity="high",
                        description=f"检测到直接相反的主张（'{w1}' vs '{w2}'）：{wrap_as_data(content_a[:30])} 与 {wrap_as_data(content_b[:30])}",
                        metadata={"opposite_pair": (w1, w2)},
                    )
                words_a = _content_tokens(content_a)
                words_b = _content_tokens(content_b)
                overlap = len(words_a & words_b)
                if overlap >= 3 or is_same_entity:
                    return ConsistencyViolation(
                        conflicting_memory_ids=[item_a.id, item_b.id],
                        violation_type="direct_contradiction",
                        severity="high",
                        description=f"检测到直接相反的主张（'{w1}' vs '{w2}'）：{wrap_as_data(content_a[:30])} 与 {wrap_as_data(content_b[:30])}",
                        metadata={"opposite_pair": (w1, w2)},
                    )

        return None

    def audit_consistency(
        self,
        memory_ids: list[str] | None = None,
        entity_name: str | None = None,
        limit: int = 50,
    ) -> ConsistencyAuditReport:
        """全局、指定实体或候选列表的信念一致性审计矩阵。"""
        if self.session is not None:
            return self._audit_consistency(self.session, memory_ids, entity_name, limit)
        with db.get_session() as s:  # 票 67：with 语义，返回即关（对齐 diben 样板）
            return self._audit_consistency(s, memory_ids, entity_name, limit)

    def _audit_consistency(
        self,
        s: Session,
        memory_ids: list[str] | None,
        entity_name: str | None,
        limit: int,
    ) -> ConsistencyAuditReport:
        """单轨实现（会话由调用方管理生命周期）。"""

        items: list[MemoryItem] = []
        if memory_ids:
            for mid in memory_ids:
                item = s.get(MemoryItem, mid)
                # 票 62：认识论态过滤——superseded 行（status 仍 active）退出审计
                if (
                    item
                    and self._check_access(item)
                    and item.status == "active"
                    and (item.lifecycle_status or "") in ("active", "weakened")
                ):
                    items.append(item)
        elif entity_name:
            query = select(MemoryItem).where(
                MemoryItem.status == "active",
                MemoryItem.lifecycle_status.in_(("active", "weakened")),
            )
            if (
                self.principal is not None
                and not self.principal.is_admin
                and self.principal.user_id
            ):
                query = query.where(
                    (MemoryItem.user_id == self.principal.user_id) | (MemoryItem.user_id.is_(None))
                )
            all_items = s.exec(query.limit(limit * 3)).all()
            for it in all_items:
                if self._check_access(it):
                    st = it.structure if isinstance(it.structure, dict) else {}
                    if (
                        st.get("entity") == entity_name
                        or entity_name in (it.content or "")
                        or entity_name in (it.key or "")
                    ):
                        items.append(it)
                        if len(items) >= limit:
                            break
        else:
            query = select(MemoryItem).where(
                MemoryItem.status == "active",
                MemoryItem.lifecycle_status.in_(("active", "weakened")),
            )
            if (
                self.principal is not None
                and not self.principal.is_admin
                and self.principal.user_id
            ):
                query = query.where(
                    (MemoryItem.user_id == self.principal.user_id) | (MemoryItem.user_id.is_(None))
                )
            all_items = s.exec(query.limit(limit * 2)).all()
            for it in all_items:
                if self._check_access(it):
                    items.append(it)
                    if len(items) >= limit:
                        break

        violations: list[ConsistencyViolation] = []
        n = len(items)

        # 1. 两两比对
        for i in range(n):
            for j in range(i + 1, n):
                v = self.detect_pairwise_contradiction(items[i], items[j], session=s)
                if v:
                    violations.append(v)

        # 2. 多跳因果链条隐性相悖推导 (Multi-hop Causal Contradiction)
        # 遍历 items 中参与因果边的节点
        for item in items:
            # 查找以 item 为 source 的因果边
            edges = s.exec(
                select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == item.id,
                    MemoryEdge.relation.in_(["causes", "leads_to"]),
                )
            ).all()
            for e in edges:
                target_node = s.get(MemoryItem, e.target_memory_id)
                # 票 62：retracted/superseded target（s.get 主键直读双绕一切
                # SQL scope）不参与多跳因果推演
                if (
                    not target_node
                    or target_node.status != "active"
                    or (target_node.lifecycle_status or "") not in ("active", "weakened")
                    or not self._check_access(target_node)
                ):
                    continue
                # 检查 target_node 是否与集合中其他记忆直接矛盾
                for other_item in items:
                    if other_item.id in (item.id, target_node.id):
                        continue
                    v_causal = self.detect_pairwise_contradiction(
                        target_node, other_item, session=s
                    )
                    if v_causal:
                        # 形成多跳因果矛盾：item -> causes -> target_node, target_node 与 other_item 相悖
                        # 记录涵盖起始前置因果节点 item
                        chain_ids = list({item.id, target_node.id, other_item.id})
                        violations.append(
                            ConsistencyViolation(
                                conflicting_memory_ids=chain_ids,
                                violation_type="multihop_causal",
                                severity="high",
                                description=(
                                    f"多跳因果相悖：前置事件 [{item.id}] 导致结论 [{target_node.id}]，"
                                    f"但与主张 [{other_item.id}] 逻辑相抵触。"
                                ),
                                metadata={
                                    "cause_id": item.id,
                                    "inferred_id": target_node.id,
                                    "opposing_id": other_item.id,
                                },
                            )
                        )

        # 去重
        unique_violations: list[ConsistencyViolation] = []
        seen = set()
        for v in violations:
            key = (tuple(sorted(v.conflicting_memory_ids)), v.violation_type)
            if key not in seen:
                seen.add(key)
                unique_violations.append(v)

        return ConsistencyAuditReport(
            total_scanned=len(items),
            violation_count=len(unique_violations),
            violations=unique_violations,
            entity_name=entity_name,
        )

    def arbitrate_deep_conflict(
        self,
        conflicting_ids: list[str],
    ) -> DeepArbitrationResult:
        """基于更漏事件轴、置信度与证据链强度的深层多方冲突仲裁直断。"""
        if self.session is not None:
            return self._arbitrate_deep_conflict(self.session, conflicting_ids)
        with db.get_session() as s:  # 票 67
            return self._arbitrate_deep_conflict(s, conflicting_ids)

    def _arbitrate_deep_conflict(
        self, s: Session, conflicting_ids: list[str]
    ) -> DeepArbitrationResult:
        """单轨实现（会话由调用方管理生命周期）。"""
        items: list[MemoryItem] = []
        for mid in conflicting_ids:
            it = s.get(MemoryItem, mid)
            # 票 62：非生效侧拒绝参与仲裁（s.get 主键直读双绕一切 SQL scope）。
            # 宁 miss 不脏写：retracted 是笔削裁定（主张错误全检索面禁用）、
            # superseded 是已取代旧值，二者都不得凭高分翻案；跳过留痕供审计。
            if (
                it
                and it.status == "active"
                and (it.lifecycle_status or "") in ("active", "weakened")
                and self._check_access(it)
            ):
                items.append(it)
            elif it is not None:
                logger.info(
                    "参商仲裁：记忆 [%s] 处于非生效态（status=%s, lifecycle=%s），"
                    "拒绝参与仲裁（宁 miss 不脏写）",
                    mid,
                    it.status,
                    it.lifecycle_status,
                )

        if not items:
            return DeepArbitrationResult(
                dominant_memory_id=None,
                subordinate_memory_ids=[],
                resolution_strategy="hold_unresolved",
                decision_trace={},
                narrative="涉事记忆均不存在或当前身份无权访问，仲裁中止（宁 miss 不脏写）。",
            )

        if len(items) == 1:
            return DeepArbitrationResult(
                dominant_memory_id=items[0].id,
                subordinate_memory_ids=[],
                resolution_strategy="supersede",
                decision_trace={"single_node": items[0].id},
                narrative=f"有效记忆仅有单条 [{items[0].id}]，自然维持生效。",
            )

        # 多条打分
        scored_items: list[tuple[MemoryItem, float, dict]] = []
        for it in items:
            score, comps = self._conflict_engine._score_with_components(it, session=s)
            scored_items.append((it, score, comps))

        # 排序：综合评分由高到低
        scored_items.sort(key=lambda x: x[1], reverse=True)
        winner_item, winner_score, winner_comps = scored_items[0]
        loser_items = scored_items[1:]

        dominant_id = winner_item.id
        subordinate_ids = [it[0].id for it in loser_items]

        # 检查是否有时态明显优势 (如晚近更漏时间轴)
        time_notes = []
        t_winner = winner_item.event_time or winner_item.created_at
        for l_it, l_sc, l_comp in loser_items:
            t_loser = l_it.event_time or l_it.created_at
            if t_winner and t_loser and t_winner > t_loser:
                time_notes.append(f"晚于 [{l_it.id}] 的事件时间")

        time_clause = f"（具有更漏事件轴时效优势：{', '.join(time_notes)}）" if time_notes else ""
        narrative = (
            f"参商深层仲裁裁决：记忆 [{dominant_id}] 胜出（综合评分 {winner_score:.3f}）{time_clause}。"
            f"建议废除或降级从属记忆：{', '.join(subordinate_ids)}。"
        )

        decision_trace = {
            "scores": {it[0].id: it[1] for it in scored_items},
            "components": {it[0].id: it[2] for it in scored_items},
            "dominant_id": dominant_id,
            "subordinate_ids": subordinate_ids,
        }

        return DeepArbitrationResult(
            dominant_memory_id=dominant_id,
            subordinate_memory_ids=subordinate_ids,
            resolution_strategy="supersede",
            decision_trace=decision_trace,
            narrative=narrative,
        )
