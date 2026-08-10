"""
LLM 输出校验冒烟测试（不 mock 网络，真实直调 validate_param_advice）。
"""
import pytest
from pydantic import ValidationError

from lantai.parameters.registry import default_snapshot
from lantai.parameters.schemas import AbstainPayload, SuggestPayload
from lantai.parameters.validation import (
    ParamValidationError,
    validate_param_advice,
)

PAPERS = [
    {"source_document_id": "raw_p1",
     "title": "Hybrid Retrieval Study",
     "source_url": "https://arxiv.org/abs/2501.00001",
     "content": ("We compare weighted fusion of dense and sparse retrieval. "
                 "A vector weight of 0.55 with BM25 weight of 0.30 improves "
                 "recall on the benchmark.")},
]

SNAP = default_snapshot()


def _suggest(conf=0.88, **over):
    base = {
        "decision": "suggest",
        "confidence": conf,
        "title": "小幅提高 BM25 权重",
        "summary": "论文建议提升稀疏检索权重",
        "rationale": "加权融合实验中 BM25 权重 0.30 效果更优",
        "expected_benefit": "hypothesis: recall 提升",
        "risk_notes": "语料差异可能不适用",
        "validation_plan": "Recall@10 + MRR@10 本地评估",
        "evidence": [
            {"source_document_id": "raw_p1",
             "quote": "A vector weight of 0.55 with BM25 weight of 0.30",
             "finding": "稀疏权重 0.30 更优",
             "applicability": "仅小步假设"}
        ],
        "changes": [
            {"name": "RETRIEVAL_W_VECTOR", "before": 0.6, "after": 0.55,
             "reason": "论文建议"},
            {"name": "RETRIEVAL_W_BM25", "before": 0.25, "after": 0.30,
             "reason": "论文建议"},
        ],
    }
    base.update(over)
    return base


def test_valid_suggest_accepted():
    payload = validate_param_advice(_suggest(), SNAP, PAPERS)
    assert isinstance(payload, SuggestPayload)
    assert payload.changes[0].after == 0.55


def test_abstain_accepted():
    payload = validate_param_advice(
        {"decision": "abstain", "reason": "证据不充分"}, SNAP, PAPERS)
    assert isinstance(payload, AbstainPayload)


def test_low_confidence_fails():
    with pytest.raises(ParamValidationError):
        validate_param_advice(_suggest(conf=0.80), SNAP, PAPERS)


def test_fake_quote_fails():
    """quote 必须是原文子串（归一化后），虚构证据拒绝。"""
    bad = _suggest()
    bad["evidence"][0]["quote"] = "We prove vector weight 0.99 is best"
    with pytest.raises(ParamValidationError):
        validate_param_advice(bad, SNAP, PAPERS)


def test_source_id_outside_batch_fails():
    bad = _suggest()
    bad["evidence"][0]["source_document_id"] = "raw_other"
    with pytest.raises(ParamValidationError):
        validate_param_advice(bad, SNAP, PAPERS)


def test_extra_field_forbidden():
    """extra=forbid：LLM 多输出字段一律拒绝。"""
    bad = _suggest()
    bad["sneaky_field"] = "injection"
    with pytest.raises(ValidationError):
        validate_param_advice(bad, SNAP, PAPERS)


def test_phantom_param_fails():
    bad = _suggest()
    bad["changes"][0]["name"] = "OPENAI_API_KEY"
    with pytest.raises(ParamValidationError):
        validate_param_advice(bad, SNAP, PAPERS)


def test_quote_whitespace_insensitive():
    """换行/多空格不影响 quote 匹配。"""
    payload = _suggest()
    payload["evidence"][0]["quote"] = "vector\n weight   of 0.55"
    result = validate_param_advice(payload, SNAP, PAPERS)
    assert isinstance(result, SuggestPayload)


def test_invalid_decision_fails():
    with pytest.raises(ParamValidationError):
        validate_param_advice({"decision": "maybe", "x": 1}, SNAP, PAPERS)
