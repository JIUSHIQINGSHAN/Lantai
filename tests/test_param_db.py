"""
DB / 队列 / 审批 / 回滚 冒烟测试——真实 SQLite，不 mock 数据库。
"""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    ParamAdvicePaper,
    ParamOverride,
    ParamSuggestion,
    RawDocument,
)
from lantai.parameters import queue, runtime, service
from lantai.parameters.registry import default_snapshot
from lantai.parameters.schemas import DecisionRequest, RollbackRequest
from lantai.parameters.validation import snapshot_hash


def _add_paper(session_factory, n=1, days_ago=0):
    """插入 n 篇 RawDocument + 入队，返回 doc_ids。"""
    doc_ids = []
    for i in range(n):
        content = f"We find retrieval weight tuning matters for paper {i}."
        doc = RawDocument(
            id=new_id("doc"), source_type="paper", source_id=f"src{i}",
            url=f"https://arxiv.org/{i}", title=f"Paper {i}",
            content=content, lang="en",
            content_hash=snapshot_hash({"i": i}),
        )
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_ids.append(doc.id)
        queue.enqueue_paper_for_param_advice(doc.id)
        if days_ago:
            with session_factory() as s:
                row = s.exec(select(ParamAdvicePaper).where(
                    ParamAdvicePaper.raw_document_id == doc.id)).first()
                if row:
                    row.available_at = utcnow() - timedelta(days=days_ago)
                    s.add(row)
                    s.commit()
    return doc_ids


def _make_suggestion(session_factory, status="pending") -> ParamSuggestion:
    """构造一条合法 pending 建议。"""
    snap = default_snapshot()
    after = dict(snap)
    after["RETRIEVAL_W_VECTOR"] = 0.55
    after["RETRIEVAL_W_BM25"] = 0.30
    run_id = new_id("par")
    sug = ParamSuggestion(
        id=new_id("psg"), run_id=run_id, status=status,
        confidence=0.9, title="t", summary="s", rationale="r",
        expected_benefit="b", risk_notes="n", validation_plan="p",
        source_document_ids=["raw_p1"],
        evidence=[{"source_document_id": "raw_p1", "quote": "q",
                   "finding": "f", "applicability": "a"}],
        changes=[{"name": "RETRIEVAL_W_VECTOR", "before": 0.6, "after": 0.55,
                  "reason": "r"},
                 {"name": "RETRIEVAL_W_BM25", "before": 0.25, "after": 0.30,
                  "reason": "r"}],
        before_snapshot=snap, after_snapshot=after,
        base_snapshot_hash=snapshot_hash(snap),
        registry_version="sha256:v", fingerprint=new_id("fp"),
    )
    with session_factory() as s:
        s.add(sug)
        s.commit()
        return sug.id


class TestTables:
    def test_four_tables_created(self, param_env):
        session_factory, engine = param_env
        names = {r[0] for r in engine.raw_connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("param_advice_run", "param_advice_paper",
                  "param_suggestion", "param_override"):
            assert t in names, f"表 {t} 未创建"


class TestQueue:
    def test_enqueue_idempotent(self, param_env):
        session_factory, _ = param_env
        ids = _add_paper(session_factory, 1)
        assert queue.enqueue_paper_for_param_advice(ids[0]) is False  # 已存在

    def test_claim_not_enough(self, param_env):
        session_factory, _ = param_env
        _add_paper(session_factory, settings.PARAM_ADVICE_MIN_PAPERS - 1)
        assert queue.claim_advice_batch() is None

    def test_claim_at_threshold(self, param_env):
        session_factory, _ = param_env
        _add_paper(session_factory, settings.PARAM_ADVICE_MIN_PAPERS)
        batch = queue.claim_advice_batch()
        assert batch is not None
        assert len(batch["paper_ids"]) == settings.PARAM_ADVICE_MIN_PAPERS
        assert batch["run_id"].startswith("par_")

    def test_claim_after_wait_days(self, param_env):
        session_factory, _ = param_env
        _add_paper(session_factory, 1, days_ago=settings.PARAM_ADVICE_MAX_WAIT_DAYS + 1)
        batch = queue.claim_advice_batch()
        assert batch is not None
        assert len(batch["paper_ids"]) == 1

    def test_recover_stale(self, param_env):
        session_factory, _ = param_env
        _add_paper(session_factory, 1)
        # 手动构造一条卡死的 processing 记录（不依赖领取窗口）
        with session_factory() as s:
            row = s.exec(select(ParamAdvicePaper)).first()
            row.state = "processing"
            row.claimed_at = utcnow() - timedelta(
                minutes=settings.PARAM_ADVICE_PROCESSING_STALE_MINUTES + 10)
            s.add(row)
            s.commit()
        assert queue.recover_stale_claims() == 1
        with session_factory() as s:
            row = s.exec(select(ParamAdvicePaper)).first()
            assert row.state == "retry"
            assert row.attempt_count == 1


class TestApproveRollback:
    def test_approve_applies_override_and_settings(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        resp = service.decide_suggestion(
            sug, DecisionRequest(decision="accepted", note="ok"),
            actor="api-key")
        assert resp.status == "accepted"
        assert resp.override.revision == 1
        # settings 原位更新
        assert settings.RETRIEVAL_W_VECTOR == 0.55
        with session_factory() as s:
            head = s.exec(select(ParamOverride).order_by(
                ParamOverride.revision.desc())).first()
            assert head.operation == "apply"
            assert head.after_snapshot["RETRIEVAL_W_BM25"] == 0.30

    def test_double_approve_conflict(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        service.decide_suggestion(sug, DecisionRequest(decision="accepted"),
                                  actor="a")
        with pytest.raises(HTTPException) as ei:
            service.decide_suggestion(sug, DecisionRequest(decision="accepted"),
                                      actor="b")
        assert ei.value.status_code == 409

    def test_reject_no_override(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        resp = service.decide_suggestion(
            sug, DecisionRequest(decision="rejected", note="语料差异大"),
            actor="api-key")
        assert resp.status == "rejected"
        with session_factory() as s:
            assert s.exec(select(ParamOverride)).first() is None

    def test_rollback_restores_before(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        applied = service.decide_suggestion(
            sug, DecisionRequest(decision="accepted"), actor="a")
        assert settings.RETRIEVAL_W_VECTOR == 0.55

        rb = service.rollback_override(
            applied.override.id,
            RollbackRequest(expected_revision=1, note="效果差"),
            actor="api-key")
        assert rb.rollback_override.revision == 2
        assert rb.effective_snapshot["RETRIEVAL_W_VECTOR"] == 0.6
        assert settings.RETRIEVAL_W_VECTOR == 0.6

    def test_rollback_non_head_conflict(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        applied = service.decide_suggestion(
            sug, DecisionRequest(decision="accepted"), actor="a")
        service.rollback_override(applied.override.id,
                                  RollbackRequest(), actor="a")
        # 再回滚同一 id：现在 head 是 rollback，且该 id 不是 head
        with pytest.raises(HTTPException) as ei:
            service.rollback_override(applied.override.id,
                                      RollbackRequest(), actor="a")
        assert ei.value.status_code == 409

    def test_revision_conflict(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        with pytest.raises(HTTPException) as ei:
            service.decide_suggestion(
                sug,
                DecisionRequest(decision="accepted", expected_revision=99),
                actor="a")
        assert ei.value.status_code == 409

    def test_snapshot_conflict_after_head_change(self, param_env):
        session_factory, _ = param_env
        sug1 = _make_suggestion(session_factory)
        sug2 = _make_suggestion(session_factory)
        service.decide_suggestion(sug1, DecisionRequest(decision="accepted"),
                                  actor="a")
        # sug2 基线过期（head 已变）→ 409
        with pytest.raises(HTTPException) as ei:
            service.decide_suggestion(sug2,
                                      DecisionRequest(decision="accepted"),
                                      actor="a")
        assert ei.value.status_code == 409
        assert "snapshot_conflict" in ei.value.detail


class TestRuntime:
    def test_effective_params_no_override(self, param_env):
        session_factory, _ = param_env
        st = service.get_effective_params()
        assert st.revision == 0
        assert st.snapshot["RETRIEVAL_W_VECTOR"] == 0.6

    def test_effective_params_after_apply(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        service.decide_suggestion(sug, DecisionRequest(decision="accepted"),
                                  actor="a")
        st = service.get_effective_params()
        assert st.revision == 1
        assert st.snapshot["RETRIEVAL_W_BM25"] == 0.30

    def test_refresh_noop_when_same_revision(self, param_env):
        session_factory, _ = param_env
        sug = _make_suggestion(session_factory)
        service.decide_suggestion(sug, DecisionRequest(decision="accepted"),
                                  actor="a")
        result = runtime.refresh_runtime_params()
        assert result["applied"] is False  # revision 未变
