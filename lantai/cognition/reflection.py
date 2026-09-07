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

        # 步骤 2：查找重复 Observation，归纳 Pattern 候选
        observations = self.db.exec(
            select(MemoryItem).where(MemoryItem.role == CognitiveRole.OBSERVATION)
        ).all()

        content_counter = Counter(obs.content.strip().lower() for obs in observations)
        repeated = {
            content: count
            for content, count in content_counter.items()
            if count > self.REPETITION_THRESHOLD
        }

        for content_key, count in repeated.items():
            source_ids = [
                obs.id for obs in observations
                if obs.content.strip().lower() == content_key
            ]
            pattern = CognitivePattern(
                id=new_id("pat"),
                pattern_type="recurrence",
                description=content_key,
                source_ids=source_ids,
                occurrence_count=count,
                independent_source_count=len(set(source_ids)),
                confidence=min(0.5 + 0.1 * count, 0.9),
                status="candidate",
                updated_at=utcnow(),
            )
            self.db.add(pattern)
            report.proposed_patterns.append(pattern)
            report.new_patterns += 1

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

        if report.new_patterns > 0 or report.failures > 0:
            report.summary = (
                f"Reflection completed: {report.new_patterns} new pattern(s) detected, "
                f"{report.failures} failure(s) on record, "
                f"{report.rules_weakened} rule(s) weakened."
            )
        else:
            report.summary = "Reflection completed: no significant changes detected."

        self.db.commit()
        return report
