from dataclasses import dataclass, field
from collections import Counter
from sqlmodel import Session, select

from lantai.models.tables import (
    MemoryItem, CognitiveRole, CognitivePattern, FailureRecord
)
from lantai.core.ids import new_id
from lantai.core.time import utcnow


@dataclass
class ReflectionReport:
    new_patterns: int = 0
    failure_patterns: int = 0
    belief_candidates: int = 0
    rule_candidates: int = 0
    rules_weakened: int = 0
    principles_under_review: int = 0
    contradictions: int = 0
    failures: int = 0
    proposed_patterns: list = field(default_factory=list)
    failure_belief_candidates: list = field(default_factory=list)
    summary: str = ""


class ReflectionEngine:
    """
    每次 Reflection 运行执行以下步骤：
    1. 统计最近的 FailureRecord，从中归纳 failure_pattern（v0.3 新增）
    2. 查找重复的 Observation 并归纳为 Pattern 候选
    3. 检查 Belief 是否有反证（置信度下降）
    4. 检查 Rule 是否有违反
    5. 晋升 Pattern→Belief，Belief→Rule（failure_pattern 用低阈值 0.55）
    """

    REPETITION_THRESHOLD = 2   # 达到此次数才视为"重复模式"
    FAILURE_PROMOTION_THRESHOLD = 0.55  # failure_pattern 低阈值（单次失败即预警）

    def __init__(self, db: Session):
        self.db = db

    def _failures_to_observations(self, failures: list[FailureRecord]) -> list[MemoryItem]:
        """
        将 FailureRecord 的 lesson/cause 字段转化为临时 OBSERVATION，
        供 detect_patterns 归纳 failure_pattern。
        临时对象不写 DB，仅用于模式检测输入。
        """
        obs = []
        for f in failures:
            if f.lesson and f.lesson.strip():
                obs.append(MemoryItem(
                    id=f"lesson_{f.id}",
                    content=f.lesson.strip(),
                    role=CognitiveRole.OBSERVATION,
                    source_ids=[f.id],
                    status="active",
                ))
            if f.cause and f.cause.strip():
                obs.append(MemoryItem(
                    id=f"cause_{f.id}",
                    content=f.cause.strip(),
                    role=CognitiveRole.OBSERVATION,
                    source_ids=[f.id],
                    status="active",
                ))
        return obs

    def run_reflection(self) -> ReflectionReport:
        report = ReflectionReport()

        # 步骤 1：统计失败记录，并从 lesson/cause 归纳 failure_pattern
        failures = self.db.exec(select(FailureRecord)).all()
        report.failures = len(failures)

        failure_belief_candidates: list[MemoryItem] = []
        if failures:
            failure_obs = self._failures_to_observations(failures)
            if failure_obs:
                from lantai.cognition.evolution import EvolutionEngine
                evo = EvolutionEngine(self.db)
                # failure_pattern 不写入 DB（内存聚类），仅用于晋升
                failure_patterns = evo.detect_patterns(failure_obs)
                report.failure_patterns = len(failure_patterns)

                # 标记为 failure_pattern 类型
                for pat in failure_patterns:
                    pat.pattern_type = "failure_pattern"

                # 低阈值晋升：0.55
                if failure_patterns:
                    fb_cands = evo.propose_beliefs(
                        failure_patterns,
                        promotion_threshold=self.FAILURE_PROMOTION_THRESHOLD,
                    )
                    for b in fb_cands:
                        # 覆盖 promoted_from 标注来源
                        if b.promotion_trace:
                            b.promotion_trace["promoted_from"] = "failure_pattern"
                        self.db.add(b)
                    failure_belief_candidates = fb_cands
                    report.failure_belief_candidates = fb_cands

        # 步骤 2：查找重复 Observation，归纳 Pattern 候选（复用 EvolutionEngine 词袋与语义签名聚类）
        observations = self.db.exec(
            select(MemoryItem).where(MemoryItem.role == CognitiveRole.OBSERVATION)
        ).all()

        from lantai.cognition.evolution import EvolutionEngine
        evo = EvolutionEngine(self.db)
        patterns = evo.detect_patterns(observations)
        report.proposed_patterns = patterns
        report.new_patterns = len(patterns)

        # 步骤 3：检查 Belief 是否置信度过低（反证衰减导致）
        beliefs = self.db.exec(
            select(MemoryItem).where(MemoryItem.role == CognitiveRole.BELIEF)
        ).all()
        for b in beliefs:
            if b.confidence < 0.4:
                report.principles_under_review += 1

        # 步骤 4：检查 Rule 是否置信度下降
        rules = self.db.exec(
            select(MemoryItem).where(MemoryItem.role == CognitiveRole.RULE)
        ).all()
        for r in rules:
            if r.confidence < 0.5:
                report.rules_weakened += 1

        # 步骤 5：使用 EvolutionEngine 从发现的 Pattern 候选晋升 Belief 候选，从 Belief 晋升 Rule 候选
        if report.proposed_patterns:
            from lantai.cognition.evolution import EvolutionEngine
            evo = EvolutionEngine(self.db)
            b_cands = evo.propose_beliefs(report.proposed_patterns)
            report.belief_candidates = len(b_cands)
            for b in b_cands:
                self.db.add(b)

            if beliefs:
                r_cands = evo.propose_rules(beliefs)
                report.rule_candidates = len(r_cands)
                for r in r_cands:
                    self.db.add(r)

        if report.new_patterns > 0 or report.failures > 0 or report.failure_patterns > 0:
            report.summary = (
                f"Reflection completed: {report.new_patterns} new pattern(s) detected, "
                f"{report.failure_patterns} failure pattern(s), "
                f"{len(failure_belief_candidates)} failure belief candidate(s), "
                f"{report.belief_candidates} belief candidate(s), "
                f"{report.failures} failure(s) on record, "
                f"{report.rules_weakened} rule(s) weakened."
            )
        else:
            report.summary = "Reflection completed: no significant changes detected."

        self.db.commit()
        return report
