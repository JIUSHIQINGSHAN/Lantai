import pytest
from sqlmodel import Session, SQLModel, create_engine
try:
    from lantai.cognition.evolution import EvolutionEngine
    from lantai.models.tables import MemoryItem, CognitiveRole, CognitivePattern
except ImportError:
    EvolutionEngine = None
    MemoryItem = None
    CognitiveRole = None
    CognitivePattern = None

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_promotion_score():
    """Verify PromotionScore rejects weak evidence."""
    assert EvolutionEngine is not None
    engine = EvolutionEngine()
    
    # Not enough independent support (0.2), low confidence (0.5)
    score = engine.calculate_promotion_score(confidence=0.5, evidence_quality=0.5, independent_support=0.2, recurrence=0.1)
    # 0.25*0.5 + 0.20*0.5 + 0.20*0.2 + 0.15*0.1 = 0.125 + 0.10 + 0.04 + 0.015 = 0.28
    assert score < 0.70
    
    # Strong evidence
    score = engine.calculate_promotion_score(confidence=0.9, evidence_quality=0.9, independent_support=0.9, recurrence=0.8)
    # 0.25*0.9 + 0.20*0.9 + 0.20*0.9 + 0.15*0.8 = 0.225 + 0.18 + 0.18 + 0.12 = 0.705
    assert score >= 0.70

def test_propose_principles():
    """Verify that proposing a principle requires scope."""
    assert EvolutionEngine is not None
    engine = EvolutionEngine()
    
    rule = MemoryItem(id="rule_1", content="Keep functions small", role=CognitiveRole.RULE, confidence=0.9)
    # Assume it meets promotion score 0.90
    proposals = engine.propose_principles([rule], mock_score=0.95)
    assert len(proposals) == 1
    prin = proposals[0]
    
    # Must have scope defined in structure
    assert prin.role == CognitiveRole.PRINCIPLE
    assert prin.status == "candidate" # Never auto-apply
    assert "scope" in prin.structure
    assert "conditions" in prin.structure["scope"]
    assert "exceptions" in prin.structure["scope"]
