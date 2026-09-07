import pytest
from sqlmodel import Session, SQLModel, create_engine

try:
    from lantai.cognition.reflection import ReflectionEngine, ReflectionReport
    from lantai.models.tables import (
        MemoryItem, CognitiveRole, CognitivePattern, FailureRecord, Evidence
    )
except ImportError:
    ReflectionEngine = None
    ReflectionReport = None
    MemoryItem = None
    CognitiveRole = None
    CognitivePattern = None
    FailureRecord = None
    Evidence = None


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_reflection_report_structure(test_db):
    """ReflectionReport 必须包含指定的结构字段。"""
    assert ReflectionEngine is not None, "ReflectionEngine is missing!"
    assert ReflectionReport is not None, "ReflectionReport is missing!"

    engine = ReflectionEngine(test_db)
    report = engine.run_reflection()

    assert hasattr(report, "new_patterns")
    assert hasattr(report, "belief_candidates")
    assert hasattr(report, "rule_candidates")
    assert hasattr(report, "rules_weakened")
    assert hasattr(report, "principles_under_review")
    assert hasattr(report, "contradictions")
    assert hasattr(report, "failures")


def test_reflection_detects_failure_lessons(test_db):
    """Reflection 必须能发现 FailureRecord 并统计。"""
    assert FailureRecord is not None
    assert ReflectionEngine is not None

    # 写入两条失败记录
    test_db.add(FailureRecord(
        id="f1",
        task="deploy",
        action="4_workers",
        expected="ok",
        actual="crash",
        severity=0.8,
        recurrence_count=1,
        source_ids=[],
    ))
    test_db.add(FailureRecord(
        id="f2",
        task="query",
        action="parallel_fts",
        expected="results",
        actual="deadlock",
        severity=0.6,
        recurrence_count=1,
        source_ids=[],
    ))
    test_db.commit()

    engine = ReflectionEngine(test_db)
    report = engine.run_reflection()

    assert report.failures == 2


def test_reflection_detects_repeated_observations(test_db):
    """重复出现的 Observation 应该被 Reflection 识别为 Pattern 候选。"""
    assert MemoryItem is not None
    assert ReflectionEngine is not None

    # 写入 3 条同类 Observation
    for i in range(3):
        test_db.add(MemoryItem(
            id=f"obs_{i}",
            content="SQLite WAL mode improves read concurrency",
            role=CognitiveRole.OBSERVATION,
        ))
    test_db.commit()

    engine = ReflectionEngine(test_db)
    report = engine.run_reflection()

    # 3 条重复同质观测，应该产生至少 1 个新模式候选
    assert report.new_patterns >= 1
