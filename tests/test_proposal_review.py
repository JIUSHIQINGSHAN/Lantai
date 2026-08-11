"""提案裁决可测量性测试：裁决原因落库（迁移 v7 / 用户裁决 / 自动拒绝）+ 回填校准输入。

测试纪律（AGENTS.md）：不 mock 内部计算逻辑——真实内存 SQLite 建表（+FTS5），
仅允许 mock LLM（chat_json/embed）与向量存储副作用（get_vector_store）。
"""
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.schemas import ProposalDecisionReq
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.storage.fts import init_fts


@contextmanager
def _patch_session(session_factory):
    original = db_module.get_session
    db_module.get_session = session_factory
    try:
        yield
    finally:
        db_module.get_session = original


@pytest.fixture()
def review_env():
    """内存 SQLite 真实建表 + FTS5 + patch db.get_session。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with _patch_session(session_factory):
        yield session_factory


def _proposal(**kw):
    defaults = dict(
        id=new_id("prop"), proposal_type="add", candidate_id=None,
        evidence_ids=["doc_1"],
        proposed_patch={"memory_type": "semantic", "key": "x",
                        "content": "y", "lane": "general"},
        reason="distill", confidence=0.8, status="pending",
    )
    defaults.update(kw)
    return MemoryProposal(**defaults)


def _mem(**kw):
    defaults = dict(
        id="", memory_type="semantic", key="", content="", lane="general",
        status="active", importance=0.5, use_count=0, helpful_count=0,
        decay_score=1.0, decay_class="episodic", confidence=0.5,
    )
    defaults.update(kw)
    if not defaults["id"]:
        defaults["id"] = new_id("mem")
    if not defaults["key"]:
        defaults["key"] = defaults["id"]
    return MemoryItem(**defaults)


def _seed(session_factory, rows):
    with session_factory() as s:
        for r in rows:
            s.add(r)
        s.commit()


# ── 迁移 v7 ──────────────────────────────────────────────────

def test_migration_v7_adds_decision_reason(tmp_path):
    """v6 老库 → apply_migrations → decision_reason 列补齐，版本前进。"""
    import sqlite3
    from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations
    path = tmp_path / "legacy-v6.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE memoryproposal (
            id TEXT PRIMARY KEY, proposal_type TEXT,
            status TEXT DEFAULT 'pending', decided_by TEXT DEFAULT 'auto'
        );
        PRAGMA user_version = 6;
    """)
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(path))
    apply_migrations(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memoryproposal)").fetchall()}
    assert "decision_reason" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    conn.close()


# ── 用户裁决 ─────────────────────────────────────────────────

class TestDecideProposal:
    """POST /proposals/{id}/decide：裁决原因落库（反馈回路输入）。"""

    def test_reject_persists_reason(self, review_env):
        """用户拒绝 → rejected + decision_reason 落库（无 mock）。"""
        session_factory = review_env
        _seed(session_factory, [_proposal(id="prop_r", confidence=0.6)])

        from lantai.services.evolution_service import decide_proposal
        res = decide_proposal("prop_r", ProposalDecisionReq(approve=False,
                                                            reason="与现有记忆冲突"))
        assert res["ok"] is True
        with session_factory() as s:
            p = s.get(MemoryProposal, "prop_r")
            assert p.status == "rejected"
            assert p.decided_by == "user"
            assert p.decision_reason == "与现有记忆冲突"

    def test_approve_persists_reason(self, review_env):
        """用户批准 → applied + decision_reason 落库（仅 mock LLM/向量副作用）。"""
        session_factory = review_env
        _seed(session_factory, [_proposal(id="prop_a", confidence=0.9)])

        from lantai.services.evolution_service import decide_proposal
        with patch("lantai.evolution.promoter.embed",
                   return_value=[[0.1] * 8]), \
                patch("lantai.retrieval.hybrid.get_vector_store",
                      return_value=Mock(add=Mock(), delete=Mock())):
            res = decide_proposal("prop_a", ProposalDecisionReq(approve=True,
                                                                reason="人工确认"))
        assert res["ok"] is True
        with session_factory() as s:
            p = s.get(MemoryProposal, "prop_a")
            assert p.status == "applied"
            assert p.decision_reason == "人工确认"


# ── 反思自动拒绝 ─────────────────────────────────────────────

class TestReflectorAutoReject:
    """自动拒绝（rejecter 不通过/高风险）→ decision_reason 记录 verdict 原因。"""

    def test_auto_reject_persists_verdict_reason(self, review_env):
        session_factory = review_env
        _seed(session_factory, [
            _mem(id="mem_old", content="公司域名是 example.com"),
            _mem(id="mem_new", content="公司域名改为 new-example.com"),
            MemoryEdge(id="edge_1", source_memory_id="mem_new",
                       target_memory_id="mem_old",
                       relation="supersedes", confidence=0.9),
        ])

        def fake_chat_json(sys_prompt, user):
            if "FLAGGED MEMORIES" in user:
                return {"proposals": [{
                    "proposal_type": "deprecate", "target_memory_id": "mem_old",
                    "evidence_ids": ["mem_new", "mem_old"], "new_content": "",
                    "reason": "被新值取代", "confidence": 0.9}]}
            return {"accept": False, "risk": "high", "reason": "证据不足"}

        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=fake_chat_json):
            result = run_reflect_once()

        assert result["discarded"] == 1
        with session_factory() as s:
            props = s.exec(select(MemoryProposal)).all()
            assert len(props) == 1
            assert props[0].status == "rejected"
            assert props[0].decision_reason == "证据不足"
