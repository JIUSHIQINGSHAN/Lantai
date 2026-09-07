from collections import Counter
from sqlmodel import Session
from lantai.models.tables import MemoryItem, CognitiveRole, CognitivePattern
from lantai.core.ids import new_id
from lantai.core.time import utcnow

class EvolutionEngine:
    def __init__(self, db: Session | None = None):
        self.db = db

    def calculate_promotion_score(
        self, 
        confidence: float, 
        evidence_quality: float, 
        independent_support: float, 
        recurrence: float,
        usefulness: float = 0.5,
        stability: float = 0.5
    ) -> float:
        """
        P = 0.25 × confidence
          + 0.20 × evidence_quality
          + 0.20 × independent_support
          + 0.15 × recurrence
          + 0.10 × usefulness
          + 0.10 × stability
        """
        p = (
            0.25 * confidence +
            0.20 * evidence_quality +
            0.20 * independent_support +
            0.15 * recurrence +
            0.10 * usefulness +
            0.10 * stability
        )
        return p

    def detect_patterns(self, experiences: list[MemoryItem]) -> list[CognitivePattern]:
        """
        对 Experience 列表做词袋聚类。
        策略：content.lower().split() 取前 5 个词为 bucket_key。
        相同 key 且出现 >= 2 次 → 生成一个 CognitivePattern（status='candidate'）。
        """
        # 建立 bucket_key -> [MemoryItem] 的映射
        buckets: dict[str, list[MemoryItem]] = {}
        for exp in experiences:
            words = exp.content.lower().split()
            key = " ".join(words[:5])
            buckets.setdefault(key, []).append(exp)

        now = utcnow()
        patterns: list[CognitivePattern] = []
        for key, items in buckets.items():
            if len(items) < 2:
                continue
            occurrence_count = len(items)
            source_ids = [sid for item in items for sid in (item.source_ids or [])]
            independent_source_count = len(set(source_ids))
            confidence = min(0.5 + 0.1 * occurrence_count, 0.9)

            pat = CognitivePattern(
                id=new_id("pat"),
                pattern_type="recurrence",
                description=key,
                source_ids=source_ids,
                occurrence_count=occurrence_count,
                independent_source_count=independent_source_count,
                confidence=confidence,
                status="candidate",
                created_at=now,
                updated_at=now,
            )
            if self.db is not None:
                self.db.add(pat)
                self.db.commit()
            patterns.append(pat)
        return patterns

    def propose_beliefs(self, patterns: list[CognitivePattern], mock_score: float = 0.0) -> list[MemoryItem]:
        proposals = []
        for pat in patterns:
            # Rule: P >= 0.70 for Belief
            score = mock_score or self.calculate_promotion_score(pat.confidence, 0.8, 0.8, 0.8)
            if score >= 0.70:
                belief = MemoryItem(
                    id=new_id("mem"),
                    content=pat.description,
                    role=CognitiveRole.BELIEF,
                    confidence=score,
                    status="candidate" # Must be candidate
                )
                proposals.append(belief)
        return proposals

    def propose_rules(self, beliefs: list[MemoryItem], mock_score: float = 0.0) -> list[MemoryItem]:
        proposals = []
        for b in beliefs:
            # Rule: P >= 0.80
            score = mock_score or self.calculate_promotion_score(b.confidence, 0.9, 0.9, 0.9)
            if score >= 0.80:
                rule = MemoryItem(
                    id=new_id("mem"),
                    content=b.content,
                    role=CognitiveRole.RULE,
                    confidence=score,
                    status="candidate"
                )
                proposals.append(rule)
        return proposals

    def propose_principles(self, rules: list[MemoryItem], mock_score: float = 0.0) -> list[MemoryItem]:
        proposals = []
        for r in rules:
            # Principle: P >= 0.90
            score = mock_score or self.calculate_promotion_score(r.confidence, 0.95, 0.95, 0.95)
            if score >= 0.90:
                prin = MemoryItem(
                    id=new_id("mem"),
                    content=r.content,
                    role=CognitiveRole.PRINCIPLE,
                    confidence=score,
                    status="candidate",
                    structure={
                        "scope": {
                            "domain": "general",
                            "conditions": [],
                            "exceptions": []
                        }
                    }
                )
                proposals.append(prin)
        return proposals
