"""Real SQLite readers/writers and detached core-service report snapshots."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.cognition.kaogong import KaogongEngine
from lantai.core.auth import Principal
from lantai.evolution.reflector import record_feedback
from lantai.models.tables import MemoryItem, MemoryUsageFeedback
from lantai.services import consolidation_service
from lantai.storage import db

pytestmark = pytest.mark.usefixtures("core_service_state")


@pytest.fixture
def core_database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'core.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    with monkeypatch.context() as mp:
        mp.setattr(db, "get_session", lambda: Session(engine))
        try:
            yield engine
        finally:
            engine.dispose()


def _seed(engine):
    with Session(engine) as session:
        session.add(
            MemoryItem(
                id="core-concurrency",
                content="Synthetic concurrent feedback memory",
                status="active",
                lane="general",
                user_id="stage5-core",
                use_count=10,
                helpful_count=10,
                importance=0.5,
            )
        )
        session.commit()


@pytest.mark.parametrize("service", ["kaogong", "consolidation"])
@pytest.mark.parametrize("source", ["getter", "cycle"])
def test_reports_return_detached_nested_snapshots(core_database, monkeypatch, service, source):
    _seed(core_database)
    principal = Principal(user_id="stage5-core", allowed_lanes=None)
    if service == "kaogong":
        engine = KaogongEngine(principal=principal)
        cycle_report = engine.run_cycle()
        get_report = engine.get_report
    else:
        monkeypatch.setattr(consolidation_service.settings, "CONSOLIDATION_AUDIT_MODE", "off")
        cycle_report = consolidation_service.run_consolidation_cycle(principal=principal)
        get_report = consolidation_service.get_consolidation_report
    report = cycle_report if source == "cycle" else get_report()
    expected = deepcopy(report)
    if service == "kaogong":
        assert expected["promoted_longterm"] == 1
        report["details"][0]["score_factors"]["composite_score"] = -100
        report["details"].clear()
    else:
        report["warnings"].append("caller-owned-warning")
        report["status"] = "caller-owned-status"
    assert get_report() == expected


def test_concurrent_feedback_writes_and_kaogong_reads_keep_exact_counters(core_database):
    _seed(core_database)
    principal = Principal(user_id="stage5-core", allowed_lanes=None)
    engine = KaogongEngine(principal=principal)
    engine.run_cycle()
    expected_report = deepcopy(engine.get_report())
    start = Barrier(8)

    def write(index):
        start.wait(timeout=10)
        return record_feedback(
            "core-concurrency",
            f"concurrent-feedback-{index}",
            helped=True,
            user_accepted=True,
            hallucination_risk=0.0,
            principal=principal,
        )

    def read(_):
        start.wait(timeout=10)
        report = engine.get_report()
        assert report == expected_report
        report["details"].clear()
        with Session(core_database) as session:
            item = session.get(MemoryItem, "core-concurrency")
            assert item.helpful_count == item.use_count
            assert 10 <= item.use_count <= 14
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        writes = [executor.submit(write, index) for index in range(4)]
        reads = [executor.submit(read, index) for index in range(4)]
        assert all(future.result(timeout=20)["ok"] for future in writes)
        assert all(future.result(timeout=20) for future in reads)

    with Session(core_database) as session:
        item = session.get(MemoryItem, "core-concurrency")
        assert item.use_count == item.helpful_count == 14
        feedbacks = session.exec(
            select(MemoryUsageFeedback).where(MemoryUsageFeedback.memory_id == item.id)
        ).all()
        assert {feedback.query for feedback in feedbacks} == {
            f"concurrent-feedback-{index}" for index in range(4)
        }
    assert engine.get_report() == expected_report
