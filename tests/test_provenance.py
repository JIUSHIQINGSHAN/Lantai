"""provenance（提取来源）测试——借鉴 TencentDB Agent Memory Roadmap：
记忆携带「哪套 prompt / 哪个模型 / 何时产出」，让"记忆质量变差"可溯源。

make_provenance 纯函数不 mock；提取链/各入口用真实内存 SQLite，仅 mock 外部
依赖（LLM chat_json、embedding、向量存储、fastpath 判定返回）。
"""
import sqlite3
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（provenance 全链路测试用）。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    from contextlib import contextmanager

    @contextmanager
    def _patch_session(session_factory):
        import lantai.storage.db as dbm
        original = dbm.get_session
        dbm.get_session = session_factory
        try:
            yield
        finally:
            dbm.get_session = original

    with _patch_session(session_factory):
        yield session_factory, engine


def _fp_data(**kw):
    data = {"topic": ["t"], "summary": "直通摘要", "claims": [], "methods": [],
            "constraints": [], "actions": [], "extractor_confidence": 1.0}
    data.update(kw)
    return data


# ── make_provenance：纯函数不 mock ─────────────────────────────


def test_make_provenance_records_prompt_model_time():
    """纯函数冒烟：记录哪套 prompt + 哪个模型 + 何时产出。"""
    from lantai.core.provenance import make_provenance
    prov = make_provenance("extract-v1")
    assert prov["prompt"] == "extract-v1"
    assert prov["model"]
    assert prov["extracted_at"]


# ── 提取入口：真实 SQLite，mock 外部 LLM/规则判定 ───────────────


def test_extraction_candidate_carries_provenance(mem_db, monkeypatch):
    """LLM 提取入口：候选 provenance.prompt == extract-v1。"""
    from lantai.models.schemas import AddMemoryReq
    session_factory, _ = mem_db
    data = _fp_data(extractor_confidence=0.9)
    monkeypatch.setattr("lantai.services.memory_service.extract_candidate",
                        lambda title, content: data)
    from lantai.services.memory_service import add_memory
    out = add_memory(AddMemoryReq(title="某研究", content="这是一篇论文的正文内容，足够长。"))
    with session_factory() as s:
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.provenance["prompt"] == "extract-v1"
        assert cand.provenance["model"]


def test_fastpath_candidate_carries_provenance(mem_db, monkeypatch):
    """fastpath 直通入口：候选 provenance.prompt == fastpath-direct（零 LLM）。"""
    from lantai.models.schemas import AddMemoryReq
    session_factory, _ = mem_db
    monkeypatch.setattr("lantai.services.memory_service.fastpath_check",
                        lambda content: _fp_data())
    from lantai.services.memory_service import add_memory
    out = add_memory(AddMemoryReq(title="规则", content="记住：部署用蓝绿发布"))
    with session_factory() as s:
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.status == "fastpath"
        assert cand.provenance["prompt"] == "fastpath-direct"


def test_dialogue_fastpath_candidate_carries_provenance(mem_db, monkeypatch):
    """对话 fastpath 入口：provenance.prompt == dialogue-fastpath。"""
    session_factory, _ = mem_db
    monkeypatch.setattr("lantai.ingestion.dialogue.fastpath_check",
                        lambda text: _fp_data(lane="general"))
    from lantai.ingestion.dialogue import ingest_dialogue
    out = ingest_dialogue("记住：部署用蓝绿发布")
    assert out["fastpath"] is True
    with session_factory() as s:
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.provenance["prompt"] == "dialogue-fastpath"


def test_dialogue_chitchat_candidate_carries_provenance(mem_db, monkeypatch):
    """对话闲聊沙汰：provenance.prompt == dialogue-chitchat（直接 rejected，ADR-0026）。"""
    session_factory, _ = mem_db
    monkeypatch.setattr("lantai.ingestion.dialogue.fastpath_check",
                        lambda text: None)
    monkeypatch.setattr("lantai.ingestion.dialogue._is_chitchat",
                        lambda text: True)
    from lantai.ingestion.dialogue import ingest_dialogue
    out = ingest_dialogue("好的好的")
    with session_factory() as s:
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.status == "rejected"
        assert cand.provenance["prompt"] == "dialogue-chitchat"


# ── 链路：candidate → proposal → MemoryItem 同源 ───────────────


def test_chain_carries_provenance_to_memory(mem_db, monkeypatch):
    """全链路：候选来源随提案继承、最终落库到 MemoryItem（可溯源到 prompt）。"""
    session_factory, _ = mem_db
    prov = {"prompt": "extract-v1", "model": "test-model", "extracted_at": "2026-08-11T00:00:00"}
    with session_factory() as s:
        s.add(MemoryCandidate(
            id="cand_p", document_id="doc_1", summary="某结论",
            claims=["结论"], actions=[], lane="general", status="new",
            provenance=prov))
        s.commit()
    with patch("lantai.evolution.proposer.chat_json",
               return_value={"proposal_type": "add", "target_key": "结论键",
                             "new_content": "某结论", "memory_type": "semantic",
                             "reason": "r", "confidence": 0.9}), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store",
               return_value=Mock(add=Mock(), delete=Mock())):
        from lantai.evolution.proposer import propose_from_candidate
        prop = propose_from_candidate("cand_p", {"decision": "promote_semantic"})
        from lantai.evolution.promoter import apply_proposal
        result = apply_proposal(prop.id)
    assert result["ok"] is True
    with session_factory() as s:
        prop2 = s.get(MemoryProposal, prop.id)
        assert prop2.provenance == prov
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "结论键")).one()
        assert mem.provenance == prov


# ── 迁移：v5 老库 → v6 ─────────────────────────────────────────


def test_migration_v6_adds_provenance_columns(tmp_path):
    """v5 老库 → v6：三表补 provenance 列 + 数据零丢失。"""
    from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations
    path = tmp_path / "v5.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, summary TEXT,"
        " status TEXT DEFAULT 'new');"
        "CREATE TABLE memoryproposal (id TEXT PRIMARY KEY, reason TEXT,"
        " status TEXT DEFAULT 'pending');"
        "CREATE TABLE memoryitem (id TEXT PRIMARY KEY, content TEXT,"
        " status TEXT DEFAULT 'active');"
    )
    conn.execute("INSERT INTO memorycandidate (id, summary) VALUES ('c1', '老候选')")
    conn.execute("INSERT INTO memoryproposal (id, reason) VALUES ('p1', '老提案')")
    conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m1', '老记忆')")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    apply_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    for table in ("memorycandidate", "memoryproposal", "memoryitem"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "provenance" in cols
    assert conn.execute(
        "SELECT summary FROM memorycandidate WHERE id='c1'").fetchone()[0] == "老候选"
    assert conn.execute(
        "SELECT content FROM memoryitem WHERE id='m1'").fetchone()[0] == "老记忆"
    conn.close()


# ── 可观测：overview 按 prompt 分布 ─────────────────────────────


def test_overview_reports_provenance_by_prompt(mem_db):
    """概览聚合：按 prompt 分组计数，无 provenance 的记忆不计入（不 mock 聚合）。"""
    from lantai.ops.overview import build_overview
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryItem(id="a", memory_type="semantic", key="a", content="A",
                         provenance={"prompt": "extract-v1"}, status="active"))
        s.add(MemoryItem(id="b", memory_type="semantic", key="b", content="B",
                         provenance={"prompt": "extract-v1"}, status="active"))
        s.add(MemoryItem(id="c", memory_type="semantic", key="c", content="C",
                         status="active"))  # 无 provenance（如 verbatim 直存）
        s.commit()
    out = build_overview(session_factory())
    assert out["provenance_by_prompt"] == {"extract-v1": 2}
