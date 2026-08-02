"""
worker 流程冒烟测试——mock 仅限外部网络（chat_json），DB/校验/状态机真实。
"""
from unittest.mock import patch

from sqlmodel import select

from remembrance.models.tables import ParamAdvicePaper, ParamSuggestion
from remembrance.parameters import queue
from remembrance.parameters.registry import default_snapshot
from remembrance.workers.param_advice_worker import run_param_advice_once

LEGAL_SUGGEST = {
    "decision": "suggest", "confidence": 0.9,
    "title": "调整 BM25 权重", "summary": "s", "rationale": "r",
    "expected_benefit": "b", "risk_notes": "n", "validation_plan": "p",
    "evidence": [{"source_document_id": "doc1",
                  "quote": "BM25 weight of 0.30 improves recall",
                  "finding": "f", "applicability": "a"}],
    "changes": [
        {"name": "RETRIEVAL_W_VECTOR", "before": 0.6, "after": 0.55,
         "reason": "r"},
        {"name": "RETRIEVAL_W_BM25", "before": 0.25, "after": 0.30,
         "reason": "r"},
    ],
}

ABSTAIN = {"decision": "abstain", "reason": "证据不足"}


def _seed_batch(param_env, n=5, content="BM25 weight of 0.30 improves recall"):
    session_factory, _ = param_env
    from remembrance.core.ids import new_id
    from remembrance.models.tables import RawDocument
    from remembrance.parameters.validation import snapshot_hash
    doc_ids = []
    for i in range(n):
        body = content if i == 0 else f"{content} variant {i}"
        doc = RawDocument(
            id=new_id("doc"), source_type="paper", source_id=f"a{i}",
            url=f"https://arxiv.org/{i}", title=f"P{i}",
            content=body, lang="en", content_hash=snapshot_hash({"i": i}))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_ids.append(doc.id)
        queue.enqueue_paper_for_param_advice(doc.id)
    return doc_ids


def _legal_suggest(doc_id: str) -> dict:
    """构造一条引用真实 doc_id 的合法建议。"""
    payload = {**LEGAL_SUGGEST}
    payload["evidence"] = [
        {"source_document_id": doc_id,
         "quote": "BM25 weight of 0.30 improves recall",
         "finding": "f", "applicability": "a"}]
    return payload


class TestWorker:
    def test_worker_produces_suggestion(self, param_env):
        session_factory, _ = param_env
        doc_ids = _seed_batch(param_env)
        with patch("remembrance.parameters.advisor.chat_json",
                   return_value=_legal_suggest(doc_ids[0])):
            run_param_advice_once()
        with session_factory() as s:
            sugs = s.exec(select(ParamSuggestion)).all()
            assert len(sugs) == 1
            assert sugs[0].status == "pending"
            assert sugs[0].changes[0]["after"] == 0.55
            rows = s.exec(select(ParamAdvicePaper)).all()
            assert all(r.state == "consumed" for r in rows)

    def test_worker_abstain_no_suggestion(self, param_env):
        session_factory, _ = param_env
        _seed_batch(param_env)
        with patch("remembrance.parameters.advisor.chat_json",
                   return_value=ABSTAIN):
            run_param_advice_once()
        with session_factory() as s:
            assert s.exec(select(ParamSuggestion)).first() is None
            rows = s.exec(select(ParamAdvicePaper)).all()
            assert all(r.state == "consumed" for r in rows)

    def test_worker_llm_error_retries(self, param_env):
        session_factory, _ = param_env
        _seed_batch(param_env)
        with patch("remembrance.parameters.advisor.chat_json",
                   side_effect=RuntimeError("network down")):
            run_param_advice_once()
        with session_factory() as s:
            assert s.exec(select(ParamSuggestion)).first() is None
            rows = s.exec(select(ParamAdvicePaper)).all()
            # 网络失败 → retry（attempt=1），不产出建议
            assert all(r.state == "retry" for r in rows)
            assert all(r.attempt_count == 1 for r in rows)

    def test_worker_invalid_output_consumed(self, param_env):
        session_factory, _ = param_env
        _seed_batch(param_env)
        with patch("remembrance.parameters.advisor.chat_json",
                   return_value={"decision": "suggest", "confidence": 0.99,
                                 "changes": [{"name": "FAKE", "before": 1,
                                              "after": 2, "reason": "x"}]}):
            run_param_advice_once()
        with session_factory() as s:
            assert s.exec(select(ParamSuggestion)).first() is None
            rows = s.exec(select(ParamAdvicePaper)).all()
            assert all(r.state == "consumed" for r in rows)  # 非法输出不重试

    def test_worker_disabled(self, param_env):
        from remembrance.core.settings import settings
        session_factory, _ = param_env
        _seed_batch(param_env, n=1)
        with patch.object(settings, "PARAM_ADVICE_ENABLED", False):
            run_param_advice_once()
        with session_factory() as s:
            assert s.exec(select(ParamSuggestion)).first() is None


def test_default_snapshot_stable():
    snap = default_snapshot()
    assert sum(snap[k] for k in ("RETRIEVAL_W_VECTOR", "RETRIEVAL_W_BM25",
                                 "RETRIEVAL_W_FTS", "RETRIEVAL_W_DECAY")) == 1.0
