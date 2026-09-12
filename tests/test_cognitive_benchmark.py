from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture(scope="function")
def engine():
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    init_fts(test_engine.raw_connection())
    return test_engine


@pytest.fixture(scope="function")
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture(scope="function")
def test_client(engine, session):
    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 384]),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 384]),
    ):
        from lantai.api.app import app

        with TestClient(app) as c:
            yield c


def _insert_memory(session, content, role=CognitiveRole.OBSERVATION, importance=1, created_at=None):
    mem = MemoryItem(
        id=new_id("mem"),
        content=content,
        role=role,
        importance=importance,
        created_at=created_at or utcnow(),
        updated_at=created_at or utcnow(),
        domain="benchmark",
    )
    session.add(mem)
    session.commit()
    sync_fts(session, mem.id, mem.content)
    return mem


def test_benchmark_memory_pollution_resilience(session, test_client):
    """
    [Benchmark] 抗污染率 (Memory Pollution Rate)
    目的：注入大量无关噪音后，精准捞出目标知识的 Recall@1。
    """
    for i in range(50):
        _insert_memory(
            session,
            f"The weather in city {i} is currently sunny with a temperature of {20 + i} degrees.",
        )

    target_content = "The core engine of Lantai is built on FastAPI and SQLite WAL."
    _insert_memory(session, target_content, role=CognitiveRole.OBSERVATION, importance=5)

    for i in range(50, 100):
        _insert_memory(
            session, f"User's favorite color in session {i} was blue.", role=CognitiveRole.BELIEF
        )

    results = hybrid_search(
        query="core engine Lantai FastAPI SQLite", top_k=5, use_rerank=False, explain=True
    )

    assert len(results) > 0, "Failed to retrieve any memories"
    assert results[0]["memory"]["content"] == target_content, (
        "Target memory is not at Rank 1 (Pollution failed)"
    )


def test_benchmark_temporal_supersedes(session, test_client):
    """
    [Benchmark] 时间变化与冲突覆盖 (Temporal Accuracy & Supersedes)
    目的：新事实覆盖旧事实时，新事实必须在召回中排序更高。
    """
    _insert_memory(
        session,
        "The CEO of the company is Alice.",
        role=CognitiveRole.OBSERVATION,
        created_at=utcnow() - timedelta(days=365),
    )
    _insert_memory(
        session,
        "The CEO of the company is Bob.",
        role=CognitiveRole.OBSERVATION,
        created_at=utcnow(),
    )

    results = hybrid_search(query="CEO company", top_k=5, use_rerank=False, explain=True)

    assert len(results) >= 2, "Failed to retrieve both facts"
    print("BOB VS ALICE RESULTS:", [(r["memory"]["content"], r.get("explain")) for r in results])
    assert "Bob" in results[0]["memory"]["content"], (
        "New fact (Bob) did not supersede old fact (Alice) at Rank 1"
    )
    assert results[0]["score"] > results[1]["score"] * 1.15, "Decay penalty for 1 year is too low"


def test_benchmark_lane_cognitive_dynamics(session, test_client):
    """
    [Benchmark] 认知动力学 (Lane Dynamics)
    目的：验证不同 Lane (Rule vs General) 在经历相同时间后的激活分数差异。
    """
    old_time = utcnow() - timedelta(days=30)
    _insert_memory(
        session,
        "Always write tests before code (TDD).",
        role=CognitiveRole.RULE,
        created_at=old_time,
    )
    _insert_memory(
        session,
        "I wrote some tests before coding today.",
        role=CognitiveRole.OBSERVATION,
        created_at=old_time,
    )

    results = hybrid_search(
        query="write tests before code TDD", top_k=5, use_rerank=False, explain=True
    )

    assert len(results) >= 2
    assert results[0]["memory"]["role"] == CognitiveRole.RULE, (
        "Rule failed to persist stronger than General memory"
    )
