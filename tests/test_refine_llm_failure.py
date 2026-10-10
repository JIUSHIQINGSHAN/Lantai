"""披沙 LLM 调用失败时不得改写候选的置信度并落库:宁 miss 不脏写。

不 mock 被测逻辑:真实内存库,仅替换外部 LLM 调用为抛错。
"""

from unittest.mock import patch

from lantai.models.tables import MemoryCandidate
from lantai.services.refine_service import refine_candidate_record


def _seed(session_factory, cand_id="cand_refine_fail"):
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id=cand_id,
                document_id="doc_refine_fail",
                summary="大哥喜欢喝绿茶",
                claims=["大哥喜欢喝绿茶"],
                actions=[],
                lane="general",
                status="pending_review",
                extractor_confidence=0.9,
                user_id="user_a",
            )
        )
        s.commit()


def test_refine_llm_failure_keeps_candidate_confidence(param_env):
    """复现:LLM 抛错时,候选的 extractor_confidence 不得被改成 0.5 落库。"""
    session_factory, _ = param_env
    _seed(session_factory)

    with patch("lantai.llm.client.chat_json", side_effect=RuntimeError("LLM offline")):
        refine_candidate_record("cand_refine_fail")

    with session_factory() as s:
        cand = s.get(MemoryCandidate, "cand_refine_fail")
        assert cand.extractor_confidence == 0.9
        assert cand.status == "pending_review"
        assert cand.summary == "大哥喜欢喝绿茶"


def test_batch_refine_llm_failure_skips_without_rejecting(param_env):
    """复现:批量入口遇到 LLM 失败时,候选保持原样,不得被驳回。"""
    from lantai.services.refine_service import batch_refine_candidates

    session_factory, _ = param_env
    _seed(session_factory, cand_id="cand_batch_fail")

    with patch("lantai.llm.client.chat_json", side_effect=RuntimeError("LLM offline")):
        with session_factory() as s:
            summary = batch_refine_candidates(min_conf=0.5, max_conf=0.95, session=s)

    with session_factory() as s:
        cand = s.get(MemoryCandidate, "cand_batch_fail")
        assert cand.status == "pending_review"
        assert cand.extractor_confidence == 0.9
    assert summary["rejected"] == 0
    assert summary["refined"] == 0
