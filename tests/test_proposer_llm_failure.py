"""提案 LLM 调用失败时不得伪造提案:宁 miss 不脏写。

不 mock 被测逻辑:真实内存库 + 真 FTS,仅替换外部 LLM 调用为抛错。
"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.models.tables import MemoryCandidate, MemoryProposal
from lantai.storage.fts import init_fts


@pytest.fixture
def llm_fail_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    with Session(engine) as s:
        s.add(
            MemoryCandidate(
                id="cand_x",
                document_id="doc_cand_x",
                summary="用户喜欢喝绿茶",
                claims=["喜欢绿茶"],
                actions=[],
                lane="general",
                status="new",
                user_id="user_a",
            )
        )
        s.commit()
    return engine


def test_llm_failure_does_not_fabricate_add_proposal(llm_fail_env):
    """复现:LLM 抛错时,不得凭候选摘要造一条 add 提案落库。"""
    from lantai.evolution.proposer import propose_from_candidate

    with patch("lantai.evolution.proposer.chat_json", side_effect=RuntimeError("llm down")):
        result = propose_from_candidate("cand_x", {"decision": "promote_semantic"})

    with Session(llm_fail_env) as s:
        props = s.exec(select(MemoryProposal)).all()
        cand = s.get(MemoryCandidate, "cand_x")

    assert result is None
    assert props == []
    assert cand.status == "pending_review"
