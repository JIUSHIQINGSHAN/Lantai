"""探颐消解闭环双索引同步冒烟（票 .scratch/review-fixes-2026-10/issues/01）。

真实栈（不 mock 冒烟纪律）：真 SQLite（StaticPool 内存库）+ 真 FTS5 trigram
（memory_fts 同事务）+ 真内嵌 ChromaDB（PersistentClient 临时目录）+ 真
resolve_probe_response 全链路（内容替换 / checkpoint / 写库 / 索引簿记均不 mock）。
唯一替身是 embed——外部 embedding 服务，AGENTS.md 纪律允许面；替身为确定性
3-gram 哈希向量（同 test_bixiao_deterministic.py：共享 gram 越多余弦越近，
使「向量已按新正文重建」的断言具备区分度，而非同值向量下的平凡命中）。

断言（票内验收三件套）：
(a) 消解闭环后 DB 正文已更新；
(b) FTS 能用新正文关键词召回该条、旧正文关键词不再命中；
(c) 向量库条目在场且已按**新正文**重建——新正文向量精确命中（cosine 距离≈0）、
    旧正文向量不再精确命中（has()/查询断言模式，参照 test_bixiao_deterministic.py）。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.services import record_ops_service as ops
from lantai.services.probing_service import resolve_probe_response
from lantai.storage.fts import init_fts, search_fts, sync_fts
from lantai.storage.vector_store import get_vector_store

_EMBED_DIM = 512

OLD_CONTENT = "公司主办公区在上海市徐汇区"
NEW_CONTENT = "公司主办公区搬迁至杭州市西湖区"
# 关键词与其 3-gram 滑窗全部只落在各自一侧正文（trigram AND 召回面有区分度）
OLD_KEYWORD = "上海市徐汇区"
NEW_KEYWORD = "杭州市西湖区"


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
def probing_env(monkeypatch, tmp_path):
    """真 SQLite + 真 FTS5 + 真内嵌 Chroma；仅 embed 走外部面替身。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(settings, "FTS_BM25_GRAM_TOKENIZE", True)
    # embed 替身挂外部面引用点：探颐消解的向量重建走
    # record_ops_service.sync_vector_upsert 的模块级 embed 绑定；源模块一并替
    # （conftest 纪律：from-import 各存一份绑定，只 patch 源模块不够）
    monkeypatch.setattr("lantai.llm.client.embed", _hash_embed)
    monkeypatch.setattr("lantai.services.record_ops_service.embed", _hash_embed)
    yield e


def _seed_conflict(engine) -> str:
    """种下真实记忆（同事务 FTS + 真向量登记）与 open 冲突账本，返回 memory_id。"""
    mid = new_id("mem")
    with Session(engine) as s:
        item = MemoryItem(
            id=mid,
            memory_type="semantic",
            key=mid,
            content=OLD_CONTENT,
            lane="general",
            user_id="u1",
            status="active",
            decay_score=1.0,
        )
        s.add(item)
        sync_fts(s, mid, OLD_CONTENT)
        s.commit()
        assert ops.sync_vector_upsert(mid, item), "seed vector upsert must succeed"
        s.add(
            ConflictEvent(
                id="conf_probe_smoke_01",
                memory_id=mid,
                incoming_ref=NEW_CONTENT,
                rule_name="office_location_conflict",
                status="open",
            )
        )
        s.commit()
    return mid


def _fts_ids(engine, query: str) -> list[str]:
    with engine.connect() as conn:
        return list(search_fts(conn.connection.driver_connection, query))


def _vector_top(query_text: str):
    """向量面直查（真 Chroma）：返回首条命中或 None。"""
    qv = _hash_embed([query_text])[0]
    hits = get_vector_store().search(qv, top_k=10)
    return hits[0] if hits else None


def test_resolve_probe_resyncs_fts_and_vector(probing_env):
    mid = _seed_conflict(probing_env)

    # 活跃基线：旧关键词 FTS 命中该条、旧正文向量精确命中（cosine 距离≈0）
    assert _fts_ids(probing_env, OLD_KEYWORD) == [mid]
    base = _vector_top(OLD_CONTENT)
    assert base is not None and base["id"] == mid and base["distance"] < 1e-6

    # 消解闭环（真实 service 全链路，principal=None 内部调用口径）
    with Session(probing_env) as s:
        res = resolve_probe_response("conf_probe_smoke_01", "是的，确实已经搬迁到杭州了", session=s)

    assert res["status"] == "resolved"
    assert res["action"] == "applied"

    # (a) DB 正文已更新、冲突账本已闭环
    with Session(probing_env) as s:
        item = s.get(MemoryItem, mid)
        conf = s.get(ConflictEvent, "conf_probe_smoke_01")
        assert item.content == NEW_CONTENT
        assert item.version == 2
        assert conf.status == "resolved"

    # (b) FTS：新正文关键词命中该条，旧正文关键词不再命中
    assert _fts_ids(probing_env, NEW_KEYWORD) == [mid]
    assert _fts_ids(probing_env, OLD_KEYWORD) == []

    # (c) 向量库：条目在场且已按新正文重建（新向量精确命中，旧向量不再精确命中）
    assert get_vector_store().has(mid) is True
    top = _vector_top(NEW_CONTENT)
    assert top is not None and top["id"] == mid and top["distance"] < 1e-6
    stale = _vector_top(OLD_CONTENT)
    assert stale is None or stale["id"] != mid or stale["distance"] > 1e-6

    # 回执：双索引同步结果如实回报（成功路径零警告，失败不静默）
    assert res["vector_synced"] is True
    assert res["warnings"] == []
