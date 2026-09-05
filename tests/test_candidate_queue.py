"""Ticket 02: Candidate Review Queue 候选可见队列

核心函数冒烟测试（不 mock 内部逻辑；仅 mock 外部网络/向量存储基础设施）：
- enqueue_rejected：reject 进待审队列 + review_due_at 落 TTL
- list_pending_candidates：只列 pending_review，按到期时间升序
- review_candidate：approve → 提案链并应用；reject → 归档
- run_candidate_ttl_once：超龄自动归档
- evolve_worker 集成：gate REJECT 不再静默丢弃
- REST 路由：GET /candidates/pending、POST /candidates/{id}/review
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import MemoryCandidate, MemoryProposal


def _make_cand(s, cand_id: str, **kw):
    c = MemoryCandidate(
        id=cand_id,
        document_id=kw.get("document_id", "doc_x"),
        summary=kw.get("summary", "candidate summary"),
        extractor_confidence=kw.get("extractor_confidence", 0.3),
        lane=kw.get("lane", "general"),
        status=kw.get("status", "new"),
        review_due_at=kw.get("review_due_at"),
    )
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


class TestEnqueueRejected:
    """reject 路径：静默丢弃 → 待审队列"""

    def test_enqueue_sets_pending_review_and_ttl(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            _make_cand(s, "cand_1", status="new")

        from lantai.services.candidate_service import enqueue_rejected
        enqueue_rejected("cand_1")

        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_1")
            assert c.status == "pending_review"
            assert c.review_due_at is not None
            expected = datetime.now(UTC) + timedelta(
                days=settings.CANDIDATE_TTL_DAYS)
            due = c.review_due_at
            if due.tzinfo is None:  # SQLite 存 naive datetime，比较前归一
                due = due.replace(tzinfo=UTC)
            assert abs((due - expected).total_seconds()) < 60

    def test_enqueue_missing_is_noop(self, param_env):
        session_factory, _ = param_env
        from lantai.services.candidate_service import enqueue_rejected
        enqueue_rejected("cand_missing")  # 不抛异常
        with session_factory() as s:
            assert s.get(MemoryCandidate, "cand_missing") is None


class TestListPending:
    """待审列表"""

    def test_only_pending_review_returned(self, param_env):
        session_factory, _ = param_env
        now = datetime.now(UTC)
        with session_factory() as s:
            _make_cand(s, "cand_p", status="pending_review",
                       review_due_at=now + timedelta(days=1))
            _make_cand(s, "cand_rej", status="rejected")
            _make_cand(s, "cand_new", status="new")

        from lantai.services.candidate_service import list_pending_candidates
        result = list_pending_candidates()
        ids = [c["id"] for c in result["candidates"]]
        assert ids == ["cand_p"]

    def test_orders_by_due_date_asc(self, param_env):
        session_factory, _ = param_env
        now = datetime.now(UTC)
        with session_factory() as s:
            _make_cand(s, "cand_late", status="pending_review",
                       review_due_at=now + timedelta(days=3))
            _make_cand(s, "cand_urgent", status="pending_review",
                       review_due_at=now + timedelta(days=1))

        from lantai.services.candidate_service import list_pending_candidates
        result = list_pending_candidates()
        ids = [c["id"] for c in result["candidates"]]
        assert ids == ["cand_urgent", "cand_late"]


class TestReview:
    """人工审核：approve → 提案链；reject → 归档"""

    def test_approve_enters_proposal_chain(self, param_env):
        session_factory, engine = param_env
        from lantai.storage.fts import init_fts
        init_fts(engine.raw_connection())
        with session_factory() as s:
            _make_cand(s, "cand_1", status="pending_review",
                       extractor_confidence=0.9,
                       review_due_at=datetime.now(UTC) + timedelta(days=1))

        with patch("lantai.evolution.proposer.chat_json", return_value={
                "proposal_type": "add", "target_key": "k1",
                "new_content": "user approved content",
                "memory_type": "semantic", "reason": "user approved",
                "confidence": 0.9}), \
             patch("lantai.evolution.promoter.embed",
                   return_value=[[0.1] * 8]), \
             patch("lantai.retrieval.hybrid.get_vector_store"):
            from lantai.services.candidate_service import review_candidate
            result = review_candidate("cand_1", approve=True)

        assert result["ok"] is True
        assert result["applied"] is False
        assert result["proposal_status"] == "pending"
        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_1")
            assert c.status == "gated"  # proposer 落状态
            props = s.exec(select(MemoryProposal)).all()
            assert len(props) == 1
            assert props[0].status == "pending"
            assert props[0].candidate_id == "cand_1"

    def test_reject_archives(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            _make_cand(s, "cand_1", status="pending_review",
                       review_due_at=datetime.now(UTC) + timedelta(days=1))

        from lantai.services.candidate_service import review_candidate
        result = review_candidate("cand_1", approve=False, reason="不应写入")

        assert result["ok"] is True
        assert result["candidate_status"] == "rejected"
        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_1")
            assert c.status == "rejected"
            assert c.review_due_at is None

    def test_review_missing_raises(self, param_env):
        session_factory, _ = param_env
        from lantai.services.candidate_service import review_candidate
        with pytest.raises(ValueError):
            review_candidate("cand_missing", approve=True)

    def test_reject_requires_reason(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            _make_cand(s, "cand_reason", status="pending_review",
                       review_due_at=datetime.now(UTC) + timedelta(days=1))
        from lantai.services.candidate_service import review_candidate
        with pytest.raises(ValueError, match="reason"):
            review_candidate("cand_reason", approve=False)


class TestDefer:
    def test_defer_and_undo_real_db(self, param_env):
        session_factory, _ = param_env
        due = datetime.now(UTC) + timedelta(days=1)
        with session_factory() as s:
            _make_cand(s, "cand_defer", status="pending_review", review_due_at=due)

        from lantai.services.candidate_service import defer_candidate, undo_candidate_defer
        deferred = defer_candidate("cand_defer", 3, expected_review_due_at=due)
        assert deferred["defer_count"] == 1
        restored = undo_candidate_defer(
            "cand_defer", datetime.fromisoformat(deferred["review_due_at"]))
        assert restored["candidate_id"] == "cand_defer"
        with session_factory() as s:
            candidate = s.get(MemoryCandidate, "cand_defer")
            actual = candidate.review_due_at.replace(tzinfo=UTC)
            assert abs((actual - due).total_seconds()) < 0.01
            assert candidate.defer_count == 0


class TestTTL:
    """超龄 pending_review 自动归档"""

    def test_expired_archived_future_kept(self, param_env):
        session_factory, _ = param_env
        now = datetime.now(UTC)
        with session_factory() as s:
            _make_cand(s, "cand_expired", status="pending_review",
                       review_due_at=now - timedelta(hours=1))
            _make_cand(s, "cand_future", status="pending_review",
                       review_due_at=now + timedelta(days=1))

        from lantai.services.candidate_service import run_candidate_ttl_once
        result = run_candidate_ttl_once()

        assert result["archived"] == 1
        with session_factory() as s:
            assert s.get(MemoryCandidate, "cand_expired").status == "rejected"
            assert s.get(MemoryCandidate, "cand_future").status == "pending_review"


class TestEvolveWorkerIntegration:
    """gate REJECT 不再静默丢弃：evolve worker 落 pending_review"""

    def test_low_confidence_reject_enqueued(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            _make_cand(s, "cand_low", status="new", extractor_confidence=0.1)

        from lantai.workers.evolve_worker import run_evolve_once
        run_evolve_once()

        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_low")
            assert c.status == "pending_review"
            assert c.review_due_at is not None


# ── REST 路由测试 ──────────────────────────────────────────────

@pytest.fixture(scope="function")
def client():
    test_engine = create_engine(
        "sqlite:///:memory:", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    from lantai.storage.fts import init_fts
    init_fts(test_engine.raw_connection())

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("lantai.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}), \
         patch("lantai.parsing.extractor.chat_json",
               return_value={"summary": "test", "claims": [], "methods": [],
                             "constraints": [], "actions": [], "topic": [],
                             "extractor_confidence": 0.8}), \
         patch("lantai.evolution.proposer.chat_json",
               return_value={"proposal_type": "add", "target_key": "k1",
                             "new_content": "approved", "memory_type": "semantic",
                             "reason": "route test", "confidence": 0.9}), \
         patch("lantai.retrieval.reranker.rerank", return_value=[]), \
         patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8]), \
         patch("lantai.evolution.promoter.embed",
               return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store"), \
         patch("lantai.storage.vector_store.ChromaVectorStore"):
        from api_server import app
        with TestClient(app) as c:
            yield c


class TestCandidateRoutes:
    """REST 入口"""

    def test_get_pending(self, client):
        resp = client.get("/candidates/pending")
        assert resp.status_code == 200
        assert resp.json()["candidates"] == []

        with db_module.get_session() as s:
            _make_cand(s, "cand_p", status="pending_review",
                       review_due_at=datetime.now(UTC) + timedelta(days=1))
        resp = client.get("/candidates/pending")
        ids = [c["id"] for c in resp.json()["candidates"]]
        assert ids == ["cand_p"]

    def test_review_approve(self, client):
        with db_module.get_session() as s:
            _make_cand(s, "cand_r", status="pending_review",
                       extractor_confidence=0.9,
                       review_due_at=datetime.now(UTC) + timedelta(days=1))
        resp = client.post("/candidates/cand_r/review", json={"approve": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["applied"] is False
        assert data["proposal_status"] == "pending"
        assert data["candidate_status"] == "gated"

    def test_review_reject(self, client):
        with db_module.get_session() as s:
            _make_cand(s, "cand_r", status="pending_review",
                       review_due_at=datetime.now(UTC) + timedelta(days=1))
        resp = client.post("/candidates/cand_r/review",
                           json={"approve": False, "reason": "不相关"})
        assert resp.status_code == 200
        assert resp.json()["candidate_status"] == "rejected"

    def test_review_missing_404(self, client):
        resp = client.post("/candidates/cand_missing/review",
                           json={"approve": True})
        assert resp.status_code == 404
