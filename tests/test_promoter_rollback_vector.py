"""promoter.rollback 向量索引重建（票 .scratch/review-fixes-2026-10/issues/04）。

根因：rollback 把 `prev.after` 逐字段 setattr 回 MemoryItem 后只调 sync_fts，
从不重建向量索引——对比同文件 add/update/merge/consolidation 分支均有
index_memory_item。回滚一条正文变过多版本的记忆后，Chroma 里仍是回滚前
正文的 embedding，语义召回错位。

与 tests/test_bixiao_deterministic.py 同一模式的不 mock 冒烟：
真 SQLite（StaticPool 内存库）+ 真 FTS5（memory_fts）+ 真内嵌 ChromaDB
（PersistentClient 指向临时目录）+ 真 apply/rollback 全链路
（apply_proposal → rollback，不 mock 任何被测函数）。

替身边界（测试纪律允许面）只有 embed（外部 embedding 服务）：确定性
字符 3-gram 哈希向量——共享 gram 越多余弦越近，回滚后向量为回滚后正文
的嵌入（距离≈0）、为回滚前正文的嵌入则距离显著大于 0，断言具备区分度。

两例：
1. test_rollback_rebuilds_vector_index —— 回滚后 FTS 与向量库均与回滚后
   正文一致（FTS 关键词召回 + 向量 has()/内容断言）；修复前红（向量库
   仍为变更前正文的 embedding）。
2. test_rollback_vector_failure_degrades_logged —— embedding 外部服务不可用
   时降级：SQL/FTS 回滚主语义不受阻断，vector_synced=False 如实回报且
   留痕不静默（口径同 record_ops_service 笔削四分法的降级面）。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.evolution.promoter import apply_proposal, rollback
from lantai.models.tables import MemoryItem, MemoryProposal
from lantai.storage.fts import init_fts, search_fts
from lantai.storage.vector_store import get_vector_store

_EMBED_DIM = 512

# 两版正文：v1/v2 各含一段独有连续短语（≥3 字，FTS 3-gram AND 语义可判），
# 共享前缀制造 gram 重叠——向量面的 v1/v2 距离 0.74、自匹配距离 0，
# 阈值 0.01 对红/绿两侧都有数量级区分度（预计算实证）。
V1 = "用户常住地在杭州滨江区，骑车上班"
V2 = "用户常住地在苏州工业园区，地铁上班"
V1_ONLY = "杭州滨江区"  # 仅 v1 含有的连续短语
V2_ONLY = "苏州工业园区"  # 仅 v2 含有的连续短语


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
def rb_env(monkeypatch, tmp_path):
    """真 SQLite + 真 FTS5 + 真内嵌 Chroma；仅 embed 走外部面替身。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    # 真实内嵌 Chroma 指向临时目录；重置模块级单例（撕卸自动还原原值）
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    # 钉住影响断言语义的档位：.env 部署配置不得渗入确定性用例
    monkeypatch.setattr(settings, "FTS_BM25_GRAM_TOKENIZE", True)
    # embed 替身挂在本用例涉及的三个外部面引用点：
    # apply（promoter）/ 回滚向量重建（record_ops_service）/ 查询（hybrid）
    monkeypatch.setattr("lantai.evolution.promoter.embed", _hash_embed)
    monkeypatch.setattr("lantai.services.record_ops_service.embed", _hash_embed)
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", _hash_embed)
    yield e


def _prop(engine, proposal_type: str, patch: dict, target: str | None = None) -> str:
    """最小真实提案：PENDING 落库，apply 走产品入口全链路。"""
    with Session(engine) as s:
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type=proposal_type,
            target_memory_id=target,
            proposed_patch=patch,
            confidence=0.9,
            provenance={"prompt": "review-fixes-04-smoke"},
        )
        s.add(prop)
        s.commit()
        return prop.id


def _apply_update_pair(engine) -> str:
    """写入记忆 → 应用一次正文变更（全部走既有 apply 路径），返回 memory_id。"""
    add_id = _prop(engine, "add", {"content": V1, "key": "home_city", "memory_type": "semantic"})
    assert apply_proposal(add_id)["ok"] is True
    with Session(engine) as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "home_city")).one()
    upd_id = _prop(
        engine,
        "update",
        {"content": V2, "key": "home_city", "memory_type": "semantic"},
        target=mem.id,
    )
    assert apply_proposal(upd_id)["ok"] is True
    return mem.id


def _fts_ids(engine, query: str) -> list[str]:
    with engine.connect() as conn:
        return list(search_fts(conn.connection.driver_connection, query))


def _top_vector_hit(vec: list[float]) -> dict | None:
    rs = get_vector_store().search(vec, top_k=5)
    return rs[0] if rs else None


def test_rollback_rebuilds_vector_index(rb_env):
    """回滚后 FTS 与向量库均与回滚后正文一致（票 04 验收本体）。"""
    mid = _apply_update_pair(rb_env)

    # 前置态校验：变更应用后 SQL/FTS/向量三面都是 v2
    with Session(rb_env) as s:
        assert s.get(MemoryItem, mid).content == V2
    assert _fts_ids(rb_env, V2_ONLY) == [mid]
    assert _fts_ids(rb_env, V1_ONLY) == []
    pre = _top_vector_hit(_hash_embed([V2])[0])
    assert pre is not None and pre["id"] == mid and pre["distance"] < 0.01

    # 回滚：走产品入口
    res = rollback(mid)
    assert res["ok"] is True, res
    assert res["vector_synced"] is True, res

    # SQL 与 FTS 与回滚后正文一致（FTS 关键词召回）
    with Session(rb_env) as s:
        assert s.get(MemoryItem, mid).content == V1
    assert _fts_ids(rb_env, V1_ONLY) == [mid]
    assert _fts_ids(rb_env, V2_ONLY) == []

    # 向量面：条目在场，且内容恰为回滚后正文的嵌入（has()/内容断言）
    assert get_vector_store().has(mid) is True
    hit = _top_vector_hit(_hash_embed([V1])[0])
    assert hit is not None and hit["id"] == mid, f"回滚后向量库未按 v1 召回：{hit}"
    assert hit["distance"] < 0.01, (
        f"向量库仍是回滚前正文的 embedding（v1 查询距离 {hit['distance']:.4f}，"
        "应为 ≈0；v1↔v2 距离预计算为 0.74）"
    )
    stale = _top_vector_hit(_hash_embed([V2])[0])
    assert stale is None or stale["id"] != mid or stale["distance"] > 0.05, (
        "回滚后 v2 的嵌入仍精确命中该记忆（向量未重建）"
    )


def test_rollback_vector_failure_degrades_logged(rb_env, monkeypatch, caplog):
    """embedding 外部服务不可用：回滚主语义不阻断，vector_synced=False 如实
    回报并留痕（降级口径同 record_ops_service 笔削四分法：响应可见，不假装干净）。"""

    def _embed_down(texts):
        raise RuntimeError("embedding service unavailable (simulated outage)")

    monkeypatch.setattr("lantai.services.record_ops_service.embed", _embed_down)

    mid = _apply_update_pair(rb_env)
    with caplog.at_level("ERROR"):
        res = rollback(mid)

    assert res["ok"] is True, res
    assert res["vector_synced"] is False, res
    # SQL 与 FTS 回滚主语义不受向量纵深失败阻断
    with Session(rb_env) as s:
        assert s.get(MemoryItem, mid).content == V1
    assert _fts_ids(rb_env, V1_ONLY) == [mid]
    assert _fts_ids(rb_env, V2_ONLY) == []
    # 留痕不静默：同步失败记 exception 日志（不吞）
    assert "vector upsert failed" in caplog.text, caplog.text
