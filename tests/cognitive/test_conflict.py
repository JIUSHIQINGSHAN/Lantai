import pytest
from sqlmodel import Session, SQLModel, create_engine

try:
    from lantai.cognition.conflicts import ConflictEngine, ConflictResolution, ConflictResult
    from lantai.core.time import utcnow
    from lantai.models.tables import CognitiveRole, Evidence, MemoryItem
except ImportError:
    ConflictEngine = None
    ConflictResult = None
    ConflictResolution = None
    MemoryItem = None
    CognitiveRole = None
    Evidence = None
    utcnow = None


def test_conflict_engine_coexist():
    """Verify ConflictEngine returns COEXIST when scopes differ."""
    assert ConflictEngine is not None

    # Principle 1 applies to task_types: small_project
    p1 = MemoryItem(
        id="p1",
        role=CognitiveRole.PRINCIPLE,
        structure={"scope": {"task_types": ["small_project"]}},
    )

    # Principle 2 applies to task_types: enterprise_app
    p2 = MemoryItem(
        id="p2",
        role=CognitiveRole.PRINCIPLE,
        structure={"scope": {"task_types": ["enterprise_app"]}},
    )

    engine = ConflictEngine()
    result = engine.resolve(p1, p2)

    assert result.resolution == ConflictResolution.COEXIST
    assert (
        "task_types" in result.reason or "scope" in result.reason.lower() or "共存" in result.reason
    )
    assert result.decision_trace is not None
    assert result.decision_trace.decision == "COEXIST"


def test_conflict_engine_win_a():
    """Verify ConflictEngine resolves by evidence strength/confidence."""
    assert ConflictEngine is not None

    # Rule 1 has high confidence
    r1 = MemoryItem(id="r1", role=CognitiveRole.RULE, confidence=0.95, structure={})

    # Rule 2 has low confidence
    r2 = MemoryItem(id="r2", role=CognitiveRole.RULE, confidence=0.40, structure={})

    engine = ConflictEngine()
    result = engine.resolve(r1, r2)

    assert result.resolution == ConflictResolution.WIN_A
    assert result.winner_id == r1.id


def test_conflict_full_score_wins_by_evidence():
    """Smoke test: 6-dim scoring — item with strong Evidence beats item with none."""
    assert ConflictEngine is not None
    assert Evidence is not None

    db_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(db_engine)

    with Session(db_engine) as session:
        now = utcnow()

        # item_a: has strong Evidence (reliability=0.9, independence=1.0)
        item_a = MemoryItem(
            id="high_ev",
            content="A with evidence",
            role=CognitiveRole.RULE,
            confidence=0.7,
            created_at=now,
            structure={"scope": {"domain": "engineering"}},
        )
        session.add(item_a)

        ev = Evidence(
            id="ev_for_high",
            evidence_type="observation",
            source_memory_id="high_ev",
            content="observed fact",
            reliability=0.9,
            independence=1.0,
        )
        session.add(ev)

        # item_b: no Evidence
        item_b = MemoryItem(
            id="no_ev",
            content="B without evidence",
            role=CognitiveRole.RULE,
            confidence=0.7,
            created_at=now,
            structure={"scope": {"domain": "engineering"}},
        )
        session.add(item_b)
        session.commit()

        result = ConflictEngine().resolve(item_a, item_b, session=session)

    assert result.resolution == ConflictResolution.WIN_A, (
        f"Expected WIN_A but got {result.resolution}: {result.reason}"
    )
