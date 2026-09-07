import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from datetime import datetime

try:
    from lantai.models.tables import FailureRecord, ActionOutcome, MemoryItem, CognitiveRole
except ImportError:
    FailureRecord = None
    ActionOutcome = None
    MemoryItem = None
    CognitiveRole = None


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_failure_record_creation(test_db):
    """FailureRecord 能够保存并关联多个 source_ids。"""
    assert FailureRecord is not None, "FailureRecord model is missing!"

    rec = FailureRecord(
        id="fail_1",
        task="deploy_lantai",
        action="enable_4_workers",
        expected="parallel processing",
        actual="SQLite contention + scheduler crash",
        cause="embedded SQLite + scheduler incompatible with multi-worker",
        lesson="keep single process in production with embedded SQLite",
        severity=0.8,
        recurrence_count=2,
        source_ids=["outcome_1", "outcome_2"],
    )
    test_db.add(rec)
    test_db.commit()

    fetched = test_db.exec(select(FailureRecord).where(FailureRecord.id == "fail_1")).first()
    assert fetched is not None
    assert fetched.task == "deploy_lantai"
    assert fetched.severity == 0.8
    assert fetched.recurrence_count == 2
    assert len(fetched.source_ids) == 2
    assert fetched.lesson != ""
    assert fetched.created_at is not None


def test_action_outcome_creation(test_db):
    """ActionOutcome 能够保存成功/失败和副作用。"""
    assert ActionOutcome is not None, "ActionOutcome model is missing!"

    outcome = ActionOutcome(
        id="out_1",
        task_id="task_deploy_001",
        action_type="deployment",
        action_summary="Deployed Lantai with 4 workers",
        success=False,
        score=0.2,
        side_effects=["SQLite lock contention", "scheduler OOM"],
        feedback={"user_note": "revert to 1 worker"},
    )
    test_db.add(outcome)
    test_db.commit()

    fetched = test_db.exec(select(ActionOutcome).where(ActionOutcome.id == "out_1")).first()
    assert fetched is not None
    assert fetched.success is False
    assert fetched.score == 0.2
    assert len(fetched.side_effects) == 2
    assert fetched.feedback["user_note"] == "revert to 1 worker"
    assert fetched.created_at is not None


def test_record_feedback_creates_failure_and_outcome(test_db, monkeypatch):
    """验证主链路反馈 (record_feedback) 在用户拒绝或高幻觉时真实生成 FailureRecord 和 ActionOutcome。"""
    from lantai.evolution.reflector import record_feedback
    from lantai.storage import db

    # 让 db.get_session() 返回 test_db
    class DummySessionCtx:
        def __enter__(self):
            return test_db
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr(db, "get_session", lambda: DummySessionCtx())

    mem = MemoryItem(id="mem_target", content="Old bad instruction", role=CognitiveRole.RULE)
    test_db.add(mem)
    test_db.commit()

    res = record_feedback(
        memory_id="mem_target",
        query="how to optimize sqlite",
        helped=False,
        user_accepted=False,
        hallucination_risk=0.8,
    )
    assert res["ok"] is True

    failures = test_db.exec(select(FailureRecord)).all()
    assert len(failures) == 1, "必须真实持久化一条 FailureRecord"
    assert "mem_target" in failures[0].source_ids
    assert failures[0].severity >= 0.8

    outcomes = test_db.exec(select(ActionOutcome)).all()
    assert len(outcomes) == 1, "必须真实持久化一条 ActionOutcome"
    assert outcomes[0].success is False
