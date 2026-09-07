from sqlmodel import Session
from lantai.models.tables import MemoryItem, CognitiveRole, CognitivePattern
from lantai.core.ids import new_id

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
        # Stub logic
        return []

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
