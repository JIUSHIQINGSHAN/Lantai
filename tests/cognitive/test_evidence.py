import pytest
from datetime import datetime
from sqlmodel import Session, SQLModel, create_engine, select

try:
    from lantai.models.tables import Evidence, MemoryItem, CognitiveRole
except ImportError:
    Evidence = None
    MemoryItem = None
    CognitiveRole = None

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_evidence_model_creation(test_db):
    """Verify Evidence can be instantiated and saved."""
    assert Evidence is not None, "Evidence model is missing!"
    
    # 1. Simulate an observation MemoryItem
    obs = MemoryItem(id="mem_obs_1", content="User prefers dark mode.", role=CognitiveRole.OBSERVATION)
    test_db.add(obs)
    
    # 2. Create Evidence pointing to the observation
    ev = Evidence(
        id="ev_1",
        evidence_type="observation",
        source_memory_id=obs.id,
        content="User toggled dark mode in settings.",
        reliability=0.8,
        independence=1.0,
        provenance={"source": "ui_telemetry"},
        status="active"
    )
    test_db.add(ev)
    test_db.commit()
    
    # 3. Retrieve and assert
    fetched_ev = test_db.exec(select(Evidence).where(Evidence.id == "ev_1")).first()
    assert fetched_ev is not None
    assert fetched_ev.evidence_type == "observation"
    assert fetched_ev.source_memory_id == "mem_obs_1"
    assert fetched_ev.reliability == 0.8
    assert fetched_ev.independence == 1.0
    assert fetched_ev.status == "active"
    assert fetched_ev.created_at is not None
    assert isinstance(fetched_ev.provenance, dict)

def test_evidence_independence_distinction(test_db):
    """Verify that multiple evidences can have different independence and reliability values."""
    assert Evidence is not None
    
    ev_repeat_1 = Evidence(
        id="ev_repeat_1",
        evidence_type="llm_statement",
        content="LLM said XYZ",
        reliability=0.5,
        independence=0.1  # Low independence because it's repeating itself
    )
    
    ev_independent_1 = Evidence(
        id="ev_indep_1",
        evidence_type="external_source",
        content="User explicit confirmation of XYZ",
        reliability=0.9,
        independence=1.0  # High independence, separate source
    )
    
    test_db.add(ev_repeat_1)
    test_db.add(ev_independent_1)
    test_db.commit()
    
    fetched = test_db.exec(select(Evidence)).all()
    assert len(fetched) == 2
    
    total_reliability = sum(e.reliability * e.independence for e in fetched)
    # The repeated statement contributes very little (0.5 * 0.1 = 0.05)
    # The independent statement contributes a lot (0.9 * 1.0 = 0.9)
    assert round(total_reliability, 2) == 0.95
