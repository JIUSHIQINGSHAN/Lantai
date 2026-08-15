"""冷启动导入测试（借鉴腾讯 L0 会话记录 + v2.0.1 时间戳修正）。

normalize_timestamp / parse_session_line 纯函数不 mock；
import_session_jsonl / 演化链时间戳继承用真实 SQLite，仅 mock 外部
依赖（LLM 提取、embedding、向量存储）。
"""
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.models.tables  # noqa: F401
from lantai.models.tables import MemoryCandidate, MemoryItem

ORIGINAL_TS = datetime(2024, 7, 3, 9, 46, 40)  # 1720000000000 epoch ms


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（导入全链路测试用）。"""
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

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

    with _patch_session(lambda: Session(engine)):
        yield lambda: Session(engine), engine


# ── 纯函数：不 mock ────────────────────────────────────────────


def test_normalize_timestamp_formats():
    """纯函数冒烟：epoch 毫秒/秒/ISO（含 Z 与时区偏移）→ naive UTC；非法抛错。"""
    from lantai.ingestion.import_service import normalize_timestamp
    assert normalize_timestamp(1720000000000) == ORIGINAL_TS
    assert normalize_timestamp(1720000000) == ORIGINAL_TS
    assert normalize_timestamp("1720000000000") == ORIGINAL_TS
    assert normalize_timestamp("2024-07-03T09:46:40Z") == ORIGINAL_TS
    assert normalize_timestamp("2024-07-03T17:46:40+08:00") == ORIGINAL_TS
    with pytest.raises(ValueError):
        normalize_timestamp(123)  # 过小（不在 epoch 秒/毫秒范围）
    with pytest.raises(ValueError):
        normalize_timestamp("not-a-time")
    with pytest.raises(ValueError):
        normalize_timestamp(None)


def test_parse_session_line_valid_and_invalid():
    """纯函数冒烟：合法行归一化；坏 JSON/缺字段/非法时间戳 → None。"""
    from lantai.ingestion.import_service import parse_session_line
    msg = parse_session_line(json.dumps(
        {"role": "user", "content": "记住：我喜欢 Python",
         "timestamp": 1720000000000, "session": "s1"}, ensure_ascii=False))
    assert msg["role"] == "user"
    assert msg["content"] == "记住：我喜欢 Python"
    assert msg["ts"] == ORIGINAL_TS
    assert msg["session"] == "s1"

    assistant = parse_session_line(json.dumps(
        {"role": "assistant", "content": "好的", "timestamp": "2024-07-03T09:46:40Z"}))
    assert assistant["role"] == "assistant"

    assert parse_session_line("not-json") is None
    assert parse_session_line("") is None
    assert parse_session_line("   ") is None
    assert parse_session_line(json.dumps({"role": "user", "content": ""})) is None
    assert parse_session_line(json.dumps(
        {"role": "system", "content": "x", "timestamp": 1720000000000})) is None
    assert parse_session_line(json.dumps(
        {"role": "user", "content": "x", "timestamp": "bad"})) is None
    assert parse_session_line(json.dumps(
        {"role": "user", "content": "x", "timestamp": 1720000000000, "session": "s1"}))["session"] == "s1"


# ── 导入入口：真实 SQLite + 真实 tmp_path 文件 ─────────────────


def test_import_session_jsonl_preserves_timestamp(mem_db, tmp_path):
    """导入：fastpath user 消息落候选，created_at=原始时间戳；assistant/坏行跳过。"""
    session_factory, _ = mem_db
    f = tmp_path / "sessions.jsonl"
    f.write_text("\n".join([
        json.dumps({"role": "user", "content": "记住：明天下午3点开会",
                    "timestamp": 1720000000000, "session": "s1"}, ensure_ascii=False),
        json.dumps({"role": "assistant", "content": "好的，已记下",
                    "timestamp": 1720000001000, "session": "s1"}, ensure_ascii=False),
        "not-json",
    ]), encoding="utf-8")

    from lantai.ingestion.import_service import import_session_jsonl
    res = import_session_jsonl(str(f))
    assert res["ok"] is True
    assert res["lines"] == 3
    assert res["errors"] == 1
    assert res["skipped_assistant"] == 1
    assert res["imported"] == 1
    assert res["would_import"] == 1
    assert res["statuses"]["fastpath"] == 1
    with session_factory() as s:
        cand = s.exec(select(MemoryCandidate)).one()
        assert cand.created_at == ORIGINAL_TS
        assert cand.provenance["prompt"] == "dialogue-session-import"


def test_import_dry_run_no_writes(mem_db, tmp_path):
    """dry-run：只解析不写库（候选表为空，零副作用）。"""
    session_factory, _ = mem_db
    f = tmp_path / "sessions.jsonl"
    f.write_text(json.dumps({"role": "user", "content": "记住：X",
                             "timestamp": 1720000000000}), encoding="utf-8")
    from lantai.ingestion.import_service import import_session_jsonl
    res = import_session_jsonl(str(f), dry_run=True)
    assert res["dry_run"] is True
    assert res["imported"] == 0
    assert res["would_import"] == 1
    with session_factory() as s:
        assert s.exec(select(MemoryCandidate)).all() == []


def test_import_extract_path_uses_import_provenance(mem_db, tmp_path):
    """LLM 提取路径：候选 provenance 同为 dialogue-session-import + 原始时间戳。"""
    session_factory, _ = mem_db
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"role": "user",
                             "content": "我最近在做分布式存储，用 Rust 写，遇到网络分区问题",
                             "timestamp": "2024-07-03T09:46:40Z"}), encoding="utf-8")
    with patch("lantai.ingestion.dialogue.extract_candidate",
               return_value={"topic": ["t"], "summary": "s", "claims": [],
                             "methods": [], "constraints": [], "actions": [],
                             "extractor_confidence": 0.9}):
        from lantai.ingestion.import_service import import_session_jsonl
        res = import_session_jsonl(str(f))
    assert res["statuses"]["new"] == 1
    with session_factory() as s:
        cand = s.exec(select(MemoryCandidate)).one()
        assert cand.created_at == ORIGINAL_TS
        assert cand.provenance["prompt"] == "dialogue-session-import"


def test_import_bad_line_does_not_stop_batch(mem_db, tmp_path):
    """单行摄取失败不拖停整批（解析失败计数，宁 miss 不脏写）。"""
    session_factory, _ = mem_db
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        json.dumps({"role": "user", "content": "记住：第一条",
                    "timestamp": 1720000000000}, ensure_ascii=False),
        "garbage",
        json.dumps({"role": "user", "content": "记住：第二条",
                    "timestamp": 1720000001000}, ensure_ascii=False),
    ]), encoding="utf-8")
    from lantai.ingestion.import_service import import_session_jsonl
    res = import_session_jsonl(str(f))
    assert res["errors"] == 1
    assert res["imported"] == 2
    with session_factory() as s:
        assert len(s.exec(select(MemoryCandidate)).all()) == 2


# ── 演化链：导入时间戳继承到 MemoryItem（时间线不压平）─────────


def _propose_and_promote(candidate_id: str):
    with patch("lantai.evolution.proposer.chat_json",
               return_value={"proposal_type": "add", "target_key": "键",
                             "new_content": "结论", "memory_type": "semantic",
                             "reason": "r", "confidence": 0.9}), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store",
               return_value=Mock(add=Mock(), delete=Mock())):
        from lantai.evolution.proposer import propose_from_candidate
        from lantai.evolution.promoter import apply_proposal
        prop = propose_from_candidate(candidate_id, {"decision": "promote_semantic"})
        return apply_proposal(prop.id)


def test_chain_carries_import_timestamp_to_memory(mem_db):
    """全链路：导入候选 → 提案 → MemoryItem.created_at = 原始时间戳。"""
    session_factory, _ = mem_db
    prov = {"prompt": "dialogue-session-import", "model": "test",
            "extracted_at": "2026-08-11T00:00:00"}
    with session_factory() as s:
        s.add(MemoryCandidate(id="cand_imp", document_id="doc_1", summary="结论",
                              claims=["结论"], actions=[], lane="general",
                              status="new", provenance=prov, created_at=ORIGINAL_TS))
        s.commit()
    result = _propose_and_promote("cand_imp")
    assert result["ok"] is True
    with session_factory() as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "键")).one()
        assert mem.created_at == ORIGINAL_TS
        assert mem.provenance["prompt"] == "dialogue-session-import"


def test_chain_does_not_override_normal_created_at(mem_db):
    """对照组：非导入 provenance 的记忆 created_at 不被覆盖（保持 promote 时刻）。"""
    session_factory, _ = mem_db
    prov = {"prompt": "extract-v1", "model": "test", "extracted_at": "2026-08-11T00:00:00"}
    with session_factory() as s:
        s.add(MemoryCandidate(id="cand_n", document_id="doc_1", summary="结论",
                              claims=["结论"], actions=[], lane="general",
                              status="new", provenance=prov))
        s.commit()
    result = _propose_and_promote("cand_n")
    assert result["ok"] is True
    with session_factory() as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "键")).one()
        assert mem.created_at is not None
        assert mem.created_at > datetime(2024, 7, 3)  # 未被压到历史时间
