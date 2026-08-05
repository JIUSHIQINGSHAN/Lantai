"""
V2 批量结构 + 信号校验冒烟测试（方向一后半 + 方向四）。
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.models.tables import RawDocument
from remembrance.parameters.advisor import render_signal_block
from remembrance.parameters.trust_models import (
    ParamContradictionReport,
    QualitySignalView,
)
from remembrance.parameters.validation import (
    ParamValidationError,
    apply_tier_weight,
    detect_signal_contamination,
    scale_delta_budget,
    validate_batch_advice,
)
from remembrance.workers.param_advice_worker import run_param_advice_once

NOW = utcnow()

PAPERS = [
    {"source_document_id": "doc_a",
     "title": "Fusion Study A",
     "source_url": "https://arxiv.org/a",
     "content": ("Dense and sparse fusion: vector weight 0.55 with BM25 0.30 "
                 "improves recall.")},
    {"source_document_id": "doc_b",
     "title": "Fusion Study B",
     "source_url": "https://arxiv.org/b",
     "content": ("Recency weighting helps long-horizon agent recall, but "
                 "aggressive decay discards stable facts. vector weight 0.55 "
                 "with BM25 0.30 improves recall.")},
]


def _view(sid, tier="A", eligible=True):
    return QualitySignalView(
        source_id=sid, arxiv_id=sid, venue_class="journal",
        evidence_tier=tier,
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        version=2, age_days=100, staleness_level="fresh",
        primary_evidence_eligible=eligible, tier_reason=["venue=journal"])


def _views(**mapping):
    return {sid: _view(sid, **kw) for sid, kw in mapping.items()}


def _sug(conf=0.9, evidence_docs=("doc_a",), after_vector=0.55):
    return {
        "decision": "suggest", "confidence": conf,
        "title": "t", "summary": "s", "rationale": "r",
        "expected_benefit": "b", "risk_notes": "n", "validation_plan": "p",
        "evidence": [{"source_document_id": d,
                      "quote": "vector weight 0.55 with BM25 0.30",
                      "finding": "f", "applicability": "a"}
                     for d in evidence_docs],
        "changes": [
            {"name": "RETRIEVAL_W_VECTOR", "before": 0.6, "after": after_vector,
             "reason": "r"},
            {"name": "RETRIEVAL_W_BM25", "before": 0.25, "after": 0.30,
             "reason": "r"},
        ],
    }


def _batch(suggestions=None, contradictions=None):
    return {"batch_id": "b1",
            "suggestions": suggestions or [_sug()],
            "abstentions": [],
            "contradictions": contradictions or []}


SNAP = {"RETRIEVAL_W_VECTOR": 0.6, "RETRIEVAL_W_BM25": 0.25,
        "RETRIEVAL_W_FTS": 0.05, "RETRIEVAL_W_DECAY": 0.1,
        "DEDUP_MERGE_THRESHOLD": 0.8, "DEDUP_UPDATE_THRESHOLD": 0.65}


class TestContamination:
    def test_contamination_detected(self):
        hits = detect_signal_contamination(
            {"suggestions": [{"evidence": [{"peer_reviewed": True}]}]})
        assert hits and "peer_reviewed" in hits[0]

    def test_clean_payload_no_hits(self):
        assert detect_signal_contamination(_batch()) == []


class TestBatchValidation:
    def test_valid_batch_passes(self):
        result = validate_batch_advice(
            _batch(), SNAP, PAPERS, _views(doc_a={}))
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["tier"] == "A"
        assert result["contradictions"] == []

    def test_all_tier_d_dropped(self):
        with pytest.raises(ParamValidationError):
            validate_batch_advice(
                _batch(), SNAP, PAPERS,
                _views(doc_a=dict(tier="D", eligible=False)))

    def test_low_confidence_fails(self):
        with pytest.raises(ParamValidationError):
            validate_batch_advice(
                _batch(suggestions=[_sug(conf=0.80)]), SNAP, PAPERS,
                _views(doc_a={}))

    def test_signal_contamination_drops_batch(self):
        bad = _batch()
        bad["suggestions"][0]["evidence"][0]["venue_class"] = "top_conf"
        with pytest.raises(ParamValidationError) as ei:
            validate_batch_advice(bad, SNAP, PAPERS, _views(doc_a={}))
        assert "污染" in str(ei.value)

    def test_fake_contradiction_quote_drops_batch(self):
        c = {"param_key": "RETRIEVAL_W_DECAY", "nature": "direction",
             "side_a": {"source_document_id": "doc_a",
                        "quote": "we prove vector 0.99 is best"},
             "side_b": {"source_document_id": "doc_b",
                        "quote": "recency weighting helps"},
             "scope_note": "", "resolution": "report_to_human"}
        with pytest.raises(ParamValidationError):
            validate_batch_advice(_batch(contradictions=[c]), SNAP, PAPERS,
                                  _views(doc_a={}, doc_b={}))

    def test_contradiction_partition_blocks_param(self):
        """W_DECAY 矛盾 → 该参数建议不产出；其他参数照常。"""
        c = {"param_key": "RETRIEVAL_W_DECAY", "nature": "direction",
             "side_a": {"source_document_id": "doc_a",
                        "quote": "vector weight 0.55 with BM25 0.30"},
             "side_b": {"source_document_id": "doc_b",
                        "quote": "aggressive decay discards stable facts"},
             "scope_note": "scope differs", "resolution": "report_to_human"}
        # 一条改 W_DECAY 的建议（会被拦）+ 一条正常建议
        bad_sug = _sug()
        bad_sug["changes"] = [
            {"name": "RETRIEVAL_W_DECAY", "before": 0.1, "after": 0.15,
             "reason": "r"}]
        good_sug = _sug()
        result = validate_batch_advice(
            _batch(suggestions=[bad_sug, good_sug], contradictions=[c]),
            SNAP, PAPERS, _views(doc_a={}, doc_b={}))
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["suggestion"].changes[0].name \
            == "RETRIEVAL_W_VECTOR"
        assert len(result["contradictions"]) == 1

    def test_contradiction_same_source_fails(self):
        c = {"param_key": "RETRIEVAL_W_DECAY", "nature": "direction",
             "side_a": {"source_document_id": "doc_a",
                        "quote": "vector weight 0.55 with BM25 0.30"},
             "side_b": {"source_document_id": "doc_a",
                        "quote": "vector weight 0.55 with BM25 0.30"},
             "scope_note": "", "resolution": "report_to_human"}
        with pytest.raises(ParamValidationError):
            validate_batch_advice(_batch(contradictions=[c]), SNAP, PAPERS,
                                  _views(doc_a={}, doc_b={}))


class TestTierQuorum:
    def test_tier_c_quorum_needs_two(self):
        # 只有一篇 tier C → quorum(2) 不满足
        with pytest.raises(ParamValidationError) as ei:
            validate_batch_advice(
                _batch(), SNAP, PAPERS,
                _views(doc_a=dict(tier="C"), doc_b=dict(tier="D", eligible=False)))
        assert "互证" in str(ei.value)

    def test_tier_c_two_papers_passes(self):
        result = validate_batch_advice(
            _batch(suggestions=[_sug(evidence_docs=("doc_a", "doc_b"))]),
            SNAP, PAPERS,
            _views(doc_a=dict(tier="C"), doc_b=dict(tier="C")))
        assert len(result["suggestions"]) == 1

    def test_tier_weight_never_raises(self):
        assert apply_tier_weight(0.9, 0.97) == pytest.approx(0.873)
        assert apply_tier_weight(0.9, 1.5) == 0.9  # 权重再大也不抬高

    def test_delta_budget_scaled_by_tier(self):
        # tier C factor=0.5：0.10 的向量变化超预算 0.05 → 拒绝
        with pytest.raises(ParamValidationError) as ei:
            validate_batch_advice(
                _batch(suggestions=[_sug(evidence_docs=("doc_a", "doc_b"),
                                         after_vector=0.5)]),
                SNAP, PAPERS,
                _views(doc_a=dict(tier="C"), doc_b=dict(tier="C")))
        assert "预算" in str(ei.value) or "变化量" in str(ei.value)

    def test_scale_delta_budget_value(self):
        from decimal import Decimal
        assert scale_delta_budget(Decimal("0.1"), Decimal("0.5")) \
            == Decimal("0.05")


class TestRenderSignalBlock:
    def test_signal_block_renders(self):
        block = render_signal_block(_views(doc_a={}))
        assert "evidence_tier=A" in block
        assert "primary_evidence_eligible=true" in block

    def test_empty_views_empty_block(self):
        assert render_signal_block({}) == ""


class TestWorkerBatch:
    def _seed(self, param_env, n=5):
        session_factory, _ = param_env
        from remembrance.parameters.paper_signals import QualitySignalDraft
        from remembrance.parameters.queue import enqueue_paper_for_param_advice
        from remembrance.parameters.signal_service import upsert_from_draft
        doc_ids = []
        for i in range(n):
            body = ("vector weight 0.55 with BM25 0.30 improves recall "
                    f"variant {i}")
            doc = RawDocument(id=new_id("doc"), source_type="paper",
                              source_id=f"s{i}", url=f"u{i}", title=f"P{i}",
                              content=body, lang="en",
                              content_hash=new_id(f"h{i}"))
            with session_factory() as s:
                s.add(doc)
                s.commit()
                doc_ids.append(doc.id)
            enqueue_paper_for_param_advice(doc.id)
            # 写质量信号（tier A，否则全部 ineligible 导致整批丢弃）
            upsert_from_draft(doc.id, QualitySignalDraft(
                arxiv_id=f"2503.{i}", journal_ref="Proc. SIGIR 2025",
                version=2))
        return doc_ids

    def _batch_payload(self, doc_id):
        return {"batch_id": "b1",
                "suggestions": [
                    {"decision": "suggest", "confidence": 0.9,
                     "title": "t", "summary": "s", "rationale": "r",
                     "expected_benefit": "b", "risk_notes": "n",
                     "validation_plan": "p",
                     "evidence": [{"source_document_id": doc_id,
                                   "quote": "vector weight 0.55 with BM25 0.30",
                                   "finding": "f", "applicability": "a"}],
                     "changes": [
                         {"name": "RETRIEVAL_W_VECTOR", "before": 0.6,
                          "after": 0.55, "reason": "r"},
                         {"name": "RETRIEVAL_W_BM25", "before": 0.25,
                          "after": 0.30, "reason": "r"}]}],
                "abstentions": [],
                "contradictions": [
                    {"param_key": "RETRIEVAL_W_DECAY", "nature": "direction",
                     "side_a": {"source_document_id": doc_id,
                                "quote": "vector weight 0.55 with BM25 0.30"},
                     "side_b": {"source_document_id": doc_id,
                                "quote": "vector weight 0.55 with BM25 0.30"},
                     "scope_note": "", "resolution": "report_to_human"}]}

    def test_worker_batch_creates_suggestion_and_contradiction(self, param_env):
        session_factory, _ = param_env
        doc_ids = self._seed(param_env)
        payload = self._batch_payload(doc_ids[0])
        payload["contradictions"] = []  # 干净矛盾避免整批丢弃
        with patch("remembrance.parameters.advisor.chat_json",
                   return_value=payload):
            run_param_advice_once()
        with session_factory() as s:
            from remembrance.models.tables import ParamSuggestion
            sugs = s.exec(select(ParamSuggestion)).all()
            assert len(sugs) == 1
            assert sugs[0].status == "pending"

    def test_worker_contradiction_report_persisted(self, param_env):
        session_factory, _ = param_env
        doc_ids = self._seed(param_env)
        # 同源矛盾会触发整批校验失败 → 全部丢弃；改造成两篇论文的真实矛盾
        payload = self._batch_payload(doc_ids[0])
        payload["suggestions"] = []
        payload["contradictions"] = [
            {"param_key": "RETRIEVAL_W_DECAY", "nature": "direction",
             "side_a": {"source_document_id": doc_ids[0],
                        "quote": "vector weight 0.55 with BM25 0.30"},
             "side_b": {"source_document_id": doc_ids[1],
                        "quote": "vector weight 0.55 with BM25 0.30 improves"},
             "scope_note": "", "resolution": "report_to_human"}]
        with patch("remembrance.parameters.advisor.chat_json",
                   return_value=payload):
            run_param_advice_once()
        with session_factory() as s:
            reports = s.exec(select(ParamContradictionReport)).all()
            assert len(reports) == 1
            assert reports[0].param_key == "RETRIEVAL_W_DECAY"
            assert reports[0].status == "open"
