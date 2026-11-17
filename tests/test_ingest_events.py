"""Real SQLite ingest attribution, stage transitions and commit-boundary regressions."""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.time import ensure_aware, utcnow
from lantai.models.tables import IngestEvent, MemoryCandidate, MemoryItem, MemoryProposal
from lantai.storage import db


@pytest.fixture
def event_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ingest.sqlite'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    monkeypatch.setattr(db, "engine", engine)
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", True)
    monkeypatch.setattr(settings, "COALESCE_ENABLED", False)
    yield engine
    engine.dispose()


def event_for(engine, result):
    with Session(engine) as s:
        return s.get(IngestEvent, result["ingest_event_id"])


def test_dialogue_fallback_conflict_and_zero_body(event_db):
    from lantai.ingestion.dialogue import ingest_dialogue

    before = utcnow()
    historical = datetime(2020, 1, 1, tzinfo=UTC)
    res = ingest_dialogue(
        "记住：蓝色杯子放在书架第二层",
        created_at=historical,
        attribution={"request_session_id": "host-A", "request_id": "call-1"},
    )
    event = event_for(event_db, res)
    assert event.outcome == "candidate"
    assert event.session_id == event.request_session_id == "host-A"
    assert event.attribution_state == "fallback"
    assert ensure_aware(event.created_at) >= before
    assert event.candidate_id == res["candidate_id"]
    with Session(event_db) as s:
        cand = s.get(MemoryCandidate, event.candidate_id)
        assert cand.session_id == "host-A"
        assert ensure_aware(cand.created_at) == historical
    other = ingest_dialogue(
        "记住：白色杯子放在书架第三层",
        session_id="origin-B",
        attribution={"request_session_id": "host-A", "request_id": "call-1"},
    )
    assert event_for(event_db, other).attribution_state == "conflict"
    assert event_for(event_db, other).session_id == "origin-B"
    assert other["ingest_event_id"] != res["ingest_event_id"]
    assert set(IngestEvent.model_fields) == {
        "id",
        "created_at",
        "updated_at",
        "user_id",
        "tenant_id",
        "agent_id",
        "session_id",
        "request_session_id",
        "request_id",
        "channel",
        "attribution_state",
        "outcome",
        "candidate_id",
        "proposal_id",
        "memory_id",
        "reason_code",
    }


def test_legitimate_rejection_and_pending_are_not_failed(event_db, monkeypatch):
    from lantai.ingestion.dialogue import ingest_dialogue
    from lantai.services.candidate_service import enqueue_rejected, review_candidate

    rejected = ingest_dialogue("好的", attribution={"request_session_id": "A"})
    assert event_for(event_db, rejected).outcome == "rejected"
    candidate = ingest_dialogue("记住：周五晚上需要复习算法")
    enqueue_rejected(candidate["candidate_id"])
    assert event_for(event_db, candidate).outcome == "pending"
    review_candidate(candidate["candidate_id"], False, "not useful")
    assert event_for(event_db, candidate).outcome == "rejected"
    assert event_for(event_db, candidate).memory_id is None


def test_event_creation_failure_prevents_business_write(event_db):
    from lantai.ingestion.dialogue import ingest_dialogue

    with event_db.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TRIGGER refuse_event BEFORE INSERT ON ingest_event BEGIN SELECT RAISE(ABORT, 'event storage unavailable'); END"
        )
    with pytest.raises(Exception):
        ingest_dialogue("记住：事件存储失败时不能报成功")
    with Session(event_db) as s:
        assert s.exec(select(MemoryCandidate)).all() == []


def test_event_link_failure_rolls_back_candidate(event_db):
    from lantai.ingestion.dialogue import ingest_dialogue

    with event_db.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TRIGGER refuse_link BEFORE UPDATE ON ingest_event WHEN NEW.candidate_id IS NOT NULL BEGIN SELECT RAISE(ABORT, 'link unavailable'); END"
        )
    with pytest.raises(Exception):
        ingest_dialogue("记住：候选与事件必须原子关联")
    with Session(event_db) as s:
        assert s.exec(select(MemoryCandidate)).all() == []
        events = s.exec(select(IngestEvent)).all()
        assert len(events) == 1
        assert events[0].outcome == "failed"
        assert events[0].reason_code == "ingest_error"


def test_candidate_proposal_apply_retains_event_and_origin(event_db, tmp_path, monkeypatch):
    from lantai.core.settings import settings
    from lantai.evolution import proposer
    from lantai.evolution.promoter import apply_proposal
    from lantai.ingestion.dialogue import ingest_dialogue
    from lantai.storage import vector_store

    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "vectors"))
    monkeypatch.setattr(vector_store, "_store", None)
    # Only the external model response is deterministic; SQL, gate ownership,
    # proposal state machine, embedding projection and checkpoints execute.
    monkeypatch.setattr(
        proposer,
        "chat_json",
        lambda *a, **kw: {
            "proposal_type": "add",
            "target_key": "event-chain-cup",
            "content": "The blue cup belongs on the second shelf.",
            "confidence": 0.9,
            "memory_type": "semantic",
            "reason": "source-supported",
        },
    )
    received = ingest_dialogue(
        "记住：蓝色杯子放在第二层",
        session_id="origin",
        user_id="alice",
        tenant_id="tenant-A",
        agent_id="agent-A",
        attribution={"request_session_id": "host", "request_id": "call"},
    )
    prop = proposer.propose_from_candidate(received["candidate_id"], {"decision": "pass"})
    assert prop is not None
    assert event_for(event_db, received).proposal_id == prop.id
    assert event_for(event_db, received).outcome == "pending"
    applied = apply_proposal(prop.id)
    assert applied["ok"] is True
    event = event_for(event_db, received)
    assert event.outcome == "created"
    assert event.memory_id == applied["memory_id"]
    with Session(event_db) as s:
        memory = s.get(MemoryItem, event.memory_id)
        assert (memory.session_id, memory.tenant_id, memory.agent_id) == (
            "origin",
            "tenant-A",
            "agent-A",
        )
    assert apply_proposal(prop.id)["ok"] is False  # no second application
    assert event_for(event_db, received).outcome == "created"


def test_event_updates_rollback_with_caller_and_cannot_rebind_actor(event_db):
    from lantai.observability.ingest_events import record_ingest_outcome, start_ingest_event

    eid = start_ingest_event(
        channel="dialogue", user_id="alice", tenant_id="tenant-A", session_id="A"
    )
    with Session(event_db) as s:
        record_ingest_outcome(s, event_ids=[eid], candidate_id="synthetic", outcome="candidate")
        s.rollback()
    with Session(event_db) as s:
        assert s.get(IngestEvent, eid).candidate_id is None
    with pytest.raises(ValueError, match="does not match"):
        start_ingest_event(
            channel="dialogue", user_id="alice", tenant_id="tenant-B", session_id="A", event_id=eid
        )
