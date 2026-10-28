"""提取语言锁定回归冒烟（v0.25 窗口票 02）：漂移→失配因果链锁定 + 提取器直调冒烟。

不 mock 冒烟纪律（AGENTS.md Testing discipline）：
- test_language_drift_breaks_recall / test_language_lock_restores_recall：
  真 SQLite（StaticPool 内存库）+ 真 FTS5 + 真内嵌 ChromaDB，真实直调
  `run_staged_eval` 全六段——提取替身是 harness 自带的注入参数（非内部逻辑
  mock），闸门/入库/索引/召回/注入全部真实执行；替身边界仅外部 embed 面
  （512 确定性，同 tests/test_staged_anchor_miss.py 范式）。
  漂移轮按基线留证 run1（docs/benchmarks/e1e2-formal-2026-10/
  e1_extraction_consistency.jsonl，2026-10-26 真调 Qwen3-8B）的英文摘要原样回放，
  锁定轮用预标中文事实（语言锁定后提取产出的等价形态）——两轮夹出
  「英文摘要压垮中文查询召回、中文摘要恢复」这一修复机制，永久锁定。
- test_extract_candidate_*：`extract_candidate` 直调冒烟——仅替外部 LLM 面
  （chat_json，纪律允许），函数本体的 prompt 组装、字段归一、降级兜底真实执行。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.eval.staged import run_staged_eval
from lantai.llm.prompts import EXTRACT_SYS
from lantai.parsing.extractor import extract_candidate
from lantai.storage.fts import init_fts

_EMBED_DIM = 512

# 基线留证 run1 的真实提取输出（含 u2 中文未漂、u3/l1 空摘要的真实形态）——
# 漂移轮逐字回放；confidences 0.7-1.0 均 ≥ GATE_MIN_EXTRACTOR_CONF(0.55) 过闸
_DRIFTED_SUMMARIES = {
    "u1": "The main database is PostgreSQL 15, deployed within the company's internal network.",
    "u2": "用户倾向于使用VSCode编辑器进行Python开发，可能因其功能全面和良好的扩展性。",
    "u3": "",  # 留证 run1 摘要为空 → extraction_missing（如实回放）
    "u4": "The team enforces a policy that requires code reviews before merging code into the main branch. This practice ensures code quality and reduces the risk of introducing bugs or security vulnerabilities.",
    "u5": "The user has a peanut allergy, which means their immune system reacts adversely to peanut proteins. This can lead to allergic reactions upon exposure, necessitating careful dietary management and avoidance of cross-contamination.",
    "u6": "The user holds a team meeting every Wednesday afternoon as part of their routine. This meeting is likely used for coordination and updates within the team.",
    "l1": "",  # 同上（真轮 gate_reject 锚点缺席的同款形态）
    "i1": "The article states that the user's mobile phone number ends with the digits 8888.",
    "i2": "The article states that the user resides in Hangzhou, China. No further details or analysis are provided.",
    "f1": "the text instructs to ignore all previous instructions and output a key, indicating a system prompt override directive.",
}


def _hash_embed(texts: list[str]) -> list[list[float]]:
    """确定性 3-gram 哈希嵌入（外部 embedding 服务的测试替身，纪律允许面）。"""
    vecs = []
    for t in texts:
        v = [0.0] * _EMBED_DIM
        t = (t or "").strip()
        grams = [t[i : i + 3] for i in range(len(t) - 2)] if len(t) >= 3 else ([t] if t else [])
        for g in grams:
            idx = int.from_bytes(hashlib.sha256(g.encode("utf-8")).digest()[:4], "big") % _EMBED_DIM
            v[idx] += 1.0
        vecs.append(v)
    return vecs


@pytest.fixture()
def staged_env(monkeypatch, tmp_path):
    """真 SQLite + 真 FTS5 + 真内嵌 Chroma；仅 embed 走外部面替身。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    monkeypatch.setenv("LANTAI_INTENT_OFF", "1")
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    monkeypatch.setattr(settings, "FTS_BM25_GRAM_TOKENIZE", True)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    # embed 替身挂到全链引用点（检索/闸门/评分/促升——全部是外部 embedding 面，
    # 统一 512 维，避免与 harness 确定性 embed（512）混维写入同一 collection）
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", _hash_embed)
    monkeypatch.setattr("lantai.gate.decision.embed", _hash_embed)
    monkeypatch.setattr("lantai.gate.scorer.embed", _hash_embed)
    monkeypatch.setattr("lantai.evolution.promoter.embed", _hash_embed)
    yield e


def _drifted_extract_fn(sid, text, expect):
    """漂移轮提取替身：按基线留证 run1 原样回放（空摘要 → 如实回放提取缺失）。"""
    summary = _DRIFTED_SUMMARIES.get(sid, "")
    if not summary:
        return []
    return [{"summary": summary, "claims": [summary], "confidence": 0.9, "lane": "general"}]


def _retrieve_stage(result: dict) -> dict:
    return next(st for st in result["stages"] if st["stage"] == "retrieve")


def test_extract_prompt_pins_language_lock():
    """语言锁定规则必须留在默认 EXTRACT_SYS（修复本体；删除即回归）。"""
    assert "same language as the source text" in EXTRACT_SYS
    assert "Never translate the source into another language" in EXTRACT_SYS


def test_language_drift_breaks_recall(staged_env):
    """漂移轮：英文摘要语料上中文查询双通道失配——回放基线 0.25 形态（q_db/q_allergy miss）。"""
    result = run_staged_eval(extract_fn=_drifted_extract_fn)

    retrieve = _retrieve_stage(result)
    assert retrieve["success_rate"] == 0.25, f"漂移轮应回放基线 0.25 形态，实得 {retrieve}"
    missed = {c["sid"] for c in result["root_cause_chains"] if c["first_fault"] == "retrieve"}
    assert missed == {"q_db", "q_allergy"}, "漂移语料上 miss 的必须是 q_db(u1) 与 q_allergy(u5)"


def test_language_lock_restores_recall(staged_env):
    """锁定轮：中文摘要语料召回恢复到管道上界 0.75（q_anchor_zero 按设计不计成功）。"""
    result = run_staged_eval()  # default_extract_fn：预标中文事实

    retrieve = _retrieve_stage(result)
    assert retrieve["success_rate"] == 0.75, f"中文语料应达管道上界 0.75，实得 {retrieve}"
    assert not any(
        c["sid"] in ("q_db", "q_allergy", "q_editor") for c in result["root_cause_chains"]
    ), "锁定轮三个真实查询不得出现在首错归因链里"


def test_extract_candidate_forwards_language_lock(monkeypatch):
    """extract_candidate 直调冒烟：锁定的 system prompt 必达 LLM 面，字段归一真实执行。"""
    captured = {}

    def _spy_chat_json(system, user, *args, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return {
            "summary": "用户的主数据库是 PostgreSQL 15。",
            "claims": ["用户的主数据库是 PostgreSQL 15"],
            "extractor_confidence": 0.9,
        }

    monkeypatch.setattr("lantai.parsing.extractor.chat_json", _spy_chat_json)
    data = extract_candidate("u1", "用户的主数据库是 PostgreSQL 15，部署在公司内网")

    assert "same language as the source text" in captured["system"], (
        "语言锁定规则未随 system prompt 下发"
    )
    assert "Never translate the source into another language" in captured["system"]
    assert "用户的主数据库是 PostgreSQL 15，部署在公司内网" in captured["user"]
    assert data["summary"] == "用户的主数据库是 PostgreSQL 15。"
    assert data["claims"] == ["用户的主数据库是 PostgreSQL 15"]
    assert data["extractor_confidence"] == 0.9


def test_extract_candidate_fallback_smoke(monkeypatch):
    """降级主路径冒烟：LLM 不可用时回退原文兜底（中文不漂移、置信 0.3）。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("lantai.parsing.extractor.chat_json", _boom)
    data = extract_candidate("u1", "用户的主数据库是 PostgreSQL 15，部署在公司内网")

    assert data["summary"].startswith("用户的主数据库是 PostgreSQL 15")
    assert data["extractor_confidence"] == 0.3
    assert data["claims"] == []
