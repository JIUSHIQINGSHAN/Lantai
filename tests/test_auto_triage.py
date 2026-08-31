"""AI 智能预审与批处理服务测试（真实 SQLite 数据库冒烟）。"""
import pytest
from sqlmodel import Session, select

from lantai.models.tables import MemoryCandidate
from lantai.services.auto_triage_service import (
    apply_ai_triage_batch,
    run_ai_triage,
    triage_candidates_batch,
)
from lantai.storage import db


@pytest.fixture(autouse=True)
def clean_candidates():
    db.init_db()
    with db.get_session() as s:
        for c in s.exec(select(MemoryCandidate)).all():
            s.delete(c)
        s.commit()
    yield
    with db.get_session() as s:
        for c in s.exec(select(MemoryCandidate)).all():
            s.delete(c)
        s.commit()


def test_triage_candidates_batch_fallback():
    """验证即使在无 LLM 时启发式保底逻辑亦能正确区分噪音与事实。"""
    sample = [
        {"id": "c1", "text": "好的收到！", "confidence": 0.1},
        {"id": "c2", "text": "用户喜欢使用 VSCode 编辑器进行 Python 开发", "confidence": 0.88},
        {"id": "c3", "text": "那个系统有点卡", "confidence": 0.45},
    ]
    results = triage_candidates_batch(sample)
    assert len(results) == 3
    rec_map = {r["id"]: r for r in results}
    assert rec_map["c1"]["action"] == "reject"
    assert rec_map["c2"]["action"] == "approve"
    assert rec_map["c3"]["action"] == "refine"


def test_run_ai_triage_and_batch_apply():
    """端到端验证 DB 扫描与批量采纳全流程。"""
    with db.get_session() as s:
        c1 = MemoryCandidate(
            id="test_cand_noise",
            document_id="doc_test_1",
            summary="嗯嗯好的",
            claims=["嗯嗯好的"],
            status="pending_review",
            extractor_confidence=0.1,
        )
        c2 = MemoryCandidate(
            id="test_cand_valid",
            document_id="doc_test_2",
            summary="用户经常在晚上 8 点提交代码",
            claims=["用户经常在晚上 8 点提交代码"],
            status="pending_review",
            extractor_confidence=0.85,
        )
        s.add(c1)
        s.add(c2)
        s.commit()

    # 1. 执行预审扫描
    triage_res = run_ai_triage(limit=10)
    assert triage_res["total"] == 2
    assert len(triage_res["recommendations"]) == 2

    # 2. 批量采纳决策
    actions = [
        {"id": "test_cand_noise", "action": "reject", "reason": "噪音测试"},
        {"id": "test_cand_valid", "action": "approve", "reason": "高质量偏好"},
    ]
    applied = apply_ai_triage_batch(actions)
    assert applied["approved"] == 1
    assert applied["rejected"] == 1
    assert applied["failed"] == 0

    # 3. 验证数据库状态更新
    with db.get_session() as s:
        cand1 = s.get(MemoryCandidate, "test_cand_noise")
        cand2 = s.get(MemoryCandidate, "test_cand_valid")
        assert cand1.status == "rejected"
        assert cand2.status == "gated"
