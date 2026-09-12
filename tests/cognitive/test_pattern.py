from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

try:
    from lantai.models.tables import CognitivePattern, CognitiveRole, MemoryItem
except ImportError:
    CognitivePattern = None
    MemoryItem = None
    CognitiveRole = None


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_cognitive_pattern_creation(test_db):
    """Verify CognitivePattern can be instantiated and saved."""
    assert CognitivePattern is not None, "CognitivePattern model is missing!"

    # Simulate a pattern that emerged from multiple experiences
    pattern = CognitivePattern(
        id="pat_1",
        pattern_type="causal",
        description="When setting parameter X, process Y finishes faster.",
        source_ids=["exp_1", "exp_2", "exp_3"],
        occurrence_count=3,
        independent_source_count=2,
        confidence=0.85,
        novelty=0.4,
        status="candidate",
    )
    test_db.add(pattern)
    test_db.commit()

    fetched = test_db.exec(select(CognitivePattern).where(CognitivePattern.id == "pat_1")).first()
    assert fetched is not None
    assert fetched.pattern_type == "causal"
    assert len(fetched.source_ids) == 3
    assert fetched.occurrence_count == 3
    assert fetched.independent_source_count == 2
    assert fetched.confidence == 0.85
    assert fetched.status == "candidate"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None
