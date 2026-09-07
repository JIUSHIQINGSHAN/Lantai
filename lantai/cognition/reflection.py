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
    belief_candidates: int = 0
    rule_candidates: int = 0
    rules_weakened: int = 0
    principles_under_review: int = 0
    contradictions: int = 0
    failures: int = 0
    proposed_patterns: list = field(default_factory=list)
    summary: str = ""


class ReflectionEngine:
    """
    每次 Reflection 运行执行以下步骤：
    1. 统计最近的 FailureRecord
    2. 查找重复的 Observation 并归纳为 Pattern 候选
    3. 检查 Belief 是否有反证（置信度下降）
    4. 检查 Rule 是否有违反
    """

    REPETITION_THRESHOLD = 2   # 达到此次数才视为"重复模式"

    def __init__(self, db: Session):
        self.db = db

    def run_reflection(self) -> ReflectionReport:
        report = ReflectionReport()

        # 步骤 1：统计失败记录
        failures = self.db.exec(select(FailureRecord)).all()
        report.failures = len(failures)

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

        if report.new_patterns > 0 or report.failures > 0:
            report.summary = (
                f"Reflection completed: {report.new_patterns} new pattern(s) detected, "
                f"{report.belief_candidates} belief candidate(s), "
                f"{report.failures} failure(s) on record, "
                f"{report.rules_weakened} rule(s) weakened."
            )
        else:
            report.summary = "Reflection completed: no significant changes detected."

        self.db.commit()
        return report
