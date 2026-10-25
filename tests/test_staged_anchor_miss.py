"""E1 staged-eval 锚点缺席（miss）测试锁定（票 02 第 1 步，验收口径第 4 条）。

不 mock 冒烟纪律：真 SQLite（StaticPool 内存库）+ 真 FTS5 + 真内嵌 ChromaDB，
真实直调 `run_staged_eval` 全六段（提取替身是 harness 自带的注入参数，闸门/
入库/索引/召回/注入全部真实执行）。替身边界仅外部 embed 面（同
tests/test_bixiao_deterministic.py 范式）。

锁定两点行为（staged.py ⑥ 注入段，约 :498-508）：
① 围栏逃逸锚点语料（sid=f1）缺席时绝不拿其他结果补位——injectable 为空、
   inject 段 samples=0，无「替考」；
② 结果记录 achieved=False 且 redirected 指向实际拦截段（extract/gate）。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.eval.staged import CORPUS, default_extract_fn, run_staged_eval
from lantai.storage.fts import init_fts

_EMBED_DIM = 512


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


def _fence_anchors(result: dict) -> list[dict]:
    return [a for a in result["anchors"] if a["name"] == "fence_escape"]


def test_miss_at_extract_no_substitution(staged_env):
    """①+② f1 在提取段缺席：无替考、achieved=False、redirected=extract。"""

    def extract_fn(sid, text, expect):
        if sid == "f1":
            return []  # f1 提取失败 → 缺席，其余语料正常
        return default_extract_fn(sid, text, expect)

    result = run_staged_eval(extract_fn=extract_fn)

    inject_stage = next(st for st in result["stages"] if st["stage"] == "inject")
    assert inject_stage["samples"] == 0, "锚点缺席时不得拿其他 indexed 结果补位注入"

    fa = _fence_anchors(result)
    assert len(fa) == 1
    assert fa[0]["achieved"] is False, "锚点缺席必须报 miss（achieved=False），不得报 achieved"
    assert fa[0]["redirected"] == "extract", "redirected 必须指向实际拦截段"
    assert fa[0]["expected_stage"] == "inject"

    # 首错归因链如实记录 f1 卡在提取段
    f1_chain = next(c for c in result["root_cause_chains"] if c["sid"] == "f1")
    assert f1_chain["first_fault"] == "extract"


def test_miss_at_gate_no_substitution(staged_env):
    """①+② f1 在闸门段被拒（低置信）：无替考、achieved=False、redirected=gate。"""

    def extract_fn(sid, text, expect):
        out = default_extract_fn(sid, text, expect)
        if sid == "f1":
            for cd in out:
                cd["confidence"] = 0.1  # 低置信 → 闸门必拒（同 l1 锚点口径）
        return out

    result = run_staged_eval(extract_fn=extract_fn)

    inject_stage = next(st for st in result["stages"] if st["stage"] == "inject")
    assert inject_stage["samples"] == 0, "锚点缺席时不得拿其他 indexed 结果补位注入"

    fa = _fence_anchors(result)
    assert len(fa) == 1
    assert fa[0]["achieved"] is False
    assert fa[0]["redirected"] == "gate", "redirected 必须指向实际拦截段"

    f1_chain = next(c for c in result["root_cause_chains"] if c["sid"] == "f1")
    assert f1_chain["first_fault"] == "gate"


def test_present_control_achieved(staged_env):
    """阳性对照：f1 正常在场 → fence_escape achieved=True（证明前两例非平凡红）。"""
    result = run_staged_eval()  # 默认提取替身，全语料在场

    fa = _fence_anchors(result)
    assert len(fa) == 1
    assert fa[0]["achieved"] is True
    assert "redirected" not in fa[0]
