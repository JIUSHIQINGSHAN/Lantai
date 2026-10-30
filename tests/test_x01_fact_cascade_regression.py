"""X-01 回归锁：完璧事实的来源约束与记忆删除级联。

锁住两件事：
1. 来源约束只作用于「有来源」的事实：source_memory_id 为空的事实照常可检索；
   来源已删除 / 已归档的事实不可检索；hybrid 不把无血统事实挂给首位结果。
2. 所有物理删除记忆的路径（终端 DELETE、promoter.delete_memory / 删文档、合并）
   都不留孤儿事实；级联失败时整体回滚，记忆不被删除。

纪律：真 SQLite + 真 FTS5，只 mock 外部存储（向量库）与 LLM 意图/嵌入。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import FactAtomic, FactEntity, MemoryEdge, MemoryItem, RawDocument
from lantai.retrieval.wanbi_retriever import search_atomic_facts_by_query

SECRET = "我的银行卡密码是 864213"


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())
    monkeypatch.setattr(db_module, "engine", engine)

    class VS:
        def __init__(self):
            self.deleted = []

        def delete(self, ids):
            self.deleted.extend(ids)

        def search(self, *a, **k):
            return []

        def add(self, *a, **k):
            return None

    vs = VS()
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: vs)
    yield engine, vs
    engine.dispose()


def _principal(user_id="u1"):
    return Principal(user_id=user_id, role="user", allowed_lanes=["general"], tenant_id=None)


def _mem(engine, mid, content, *, status="active", user_id="u1"):
    from lantai.storage.fts import sync_fts

    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=content,
                lane="general",
                user_id=user_id,
                status=status,
                importance=0.9,
                decay_score=1.0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()
        sync_fts(s, mid, content)
        s.commit()


def _fact(engine, fid, statement, source, entities=("银行卡",), user_id="u1"):
    with Session(engine) as s:
        s.add(
            FactAtomic(
                id=fid,
                user_id=user_id,
                statement=statement,
                predicate="has",
                confidence=0.9,
                source_memory_id=source,
            )
        )
        for e in entities:
            s.add(FactEntity(fact_id=fid, entity_name=e, role="subject"))
        s.commit()


def _facts_of(engine, source):
    with Session(engine) as s:
        return s.exec(select(FactAtomic).where(FactAtomic.source_memory_id == source)).all()


def _entities_of(engine, fid):
    with Session(engine) as s:
        return s.exec(select(FactEntity).where(FactEntity.fact_id == fid)).all()


def _exists(engine, mid):
    with Session(engine) as s:
        return s.get(MemoryItem, mid) is not None


# ── 1. 来源约束 ──────────────────────────────────────────────


class TestSourceConstraint:
    def test_sourceless_fact_still_retrievable(self, env):
        engine, _ = env
        _fact(engine, "f-none", "甲公司与乙公司签署合作协议", None, entities=("甲公司",))
        with Session(engine) as s:
            res = search_atomic_facts_by_query(s, "甲公司与乙公司签署")
        assert [f["id"] for f in res] == ["f-none"]

    def test_fact_with_archived_source_hidden(self, env):
        engine, _ = env
        _mem(engine, "m-arch", "甲公司旧记录", status="archived")
        _fact(engine, "f-arch", "甲公司与乙公司签署合作协议", "m-arch", entities=("甲公司",))
        with Session(engine) as s:
            assert search_atomic_facts_by_query(s, "甲公司与乙公司签署") == []

    def test_fact_with_dangling_source_hidden(self, env):
        """来源记忆行已不存在（历史孤儿）：LEFT JOIN 得 NULL，必须排除。"""
        engine, _ = env
        _fact(engine, "f-dang", "甲公司与乙公司签署合作协议", "m-gone", entities=("甲公司",))
        with Session(engine) as s:
            assert search_atomic_facts_by_query(s, "甲公司与乙公司签署") == []

    def test_hybrid_does_not_attach_unrelated_fact_to_top_result(self, env):
        """无血统事实不挂给首位结果（X-01 原始泄漏形态）。"""
        engine, _ = env
        _mem(engine, "m-tea", "我的爱好是喝乌龙茶，我的银行卡是招商的", user_id=None)
        _fact(engine, "f-none", SECRET, None, entities=("银行卡",), user_id=None)
        from lantai.retrieval import hybrid

        with (
            patch(
                "lantai.retrieval.intent.chat_json",
                return_value={"intent": "fact_lookup", "candidate_n": 5},
            ),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        ):
            res = hybrid.hybrid_search("我的银行卡", top_k=5, use_rerank=False)
        assert res, "前置：乌龙茶记忆应被召回"
        attached = [f["statement"] for r in res for f in r.get("atomic_facts", [])]
        assert SECRET not in attached


# ── 2. 删除级联 ──────────────────────────────────────────────


class TestDeleteCascade:
    def test_promoter_delete_purges_facts(self, env):
        from lantai.evolution.promoter import delete_memory

        engine, vs = env
        _mem(engine, "m-sec", SECRET)
        _fact(engine, "f-sec", SECRET, "m-sec")
        out = delete_memory("m-sec", principal=_principal())
        assert out == {"ok": True}
        assert not _exists(engine, "m-sec")
        assert _facts_of(engine, "m-sec") == []
        assert _entities_of(engine, "f-sec") == []
        assert "m-sec" in vs.deleted

    def test_delete_document_purges_facts(self, env):
        """删文档 -> source_service.delete_document -> promoter.delete_memory。"""
        from lantai.services.source_service import delete_document

        engine, _ = env
        _mem(engine, "m-doc", SECRET)
        _fact(engine, "f-doc", SECRET, "m-doc")
        with Session(engine) as s:
            s.add(
                RawDocument(
                    id="doc-1",
                    user_id="u1",
                    source_type="manual",
                    source_id="doc-1",
                    url="file://doc-1",
                    title="doc-1",
                    content="文档正文",
                    content_hash="hash-doc-1",
                )
            )
            s.add(
                MemoryEdge(
                    id="e-1",
                    source_memory_id="doc-1",
                    target_memory_id="m-doc",
                    relation="extract",
                    confidence=0.9,
                    user_id="u1",
                )
            )
            s.commit()
        out = delete_document("doc-1", principal=_principal())
        assert out.get("ok") is True, out
        assert not _exists(engine, "m-doc")
        assert _facts_of(engine, "m-doc") == []

    def test_promoter_cascade_failure_keeps_memory(self, env, monkeypatch):
        from lantai.evolution.promoter import delete_memory

        engine, vs = env
        _mem(engine, "m-keep", SECRET)
        _fact(engine, "f-keep", SECRET, "m-keep")

        def boom(*a, **k):
            raise RuntimeError("injected")

        monkeypatch.setattr("lantai.services.record_ops_service.purge_facts_for_memory", boom)
        out = delete_memory("m-keep", principal=_principal())
        assert out["ok"] is False and "cascade" in out["reason"]
        assert _exists(engine, "m-keep")
        assert len(_facts_of(engine, "m-keep")) == 1
        assert "m-keep" not in vs.deleted, "SQL 回滚了，向量库不能先删"

    def test_terminal_delete_purges_facts(self, env):
        engine, vs = env
        _mem(engine, "m-t", SECRET)
        _fact(engine, "f-t", SECRET, "m-t")
        app.dependency_overrides[get_current_user] = lambda: _principal()
        try:
            with TestClient(app) as c:
                r = c.delete("/terminal/memory/m-t")
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 200, r.text
        assert not _exists(engine, "m-t")
        assert _facts_of(engine, "m-t") == []
        assert _entities_of(engine, "f-t") == []

    def test_terminal_cascade_failure_keeps_memory(self, env, monkeypatch):
        engine, vs = env
        _mem(engine, "m-t2", SECRET)
        _fact(engine, "f-t2", SECRET, "m-t2")

        def boom(*a, **k):
            raise RuntimeError("injected")

        monkeypatch.setattr("lantai.services.record_ops_service.purge_facts_for_memory", boom)
        app.dependency_overrides[get_current_user] = lambda: _principal()
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                r = c.delete("/terminal/memory/m-t2")
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 500, r.text
        assert _exists(engine, "m-t2")
        assert len(_facts_of(engine, "m-t2")) == 1
        assert "m-t2" not in vs.deleted

    def test_merge_repoints_facts_to_target(self, env):
        engine, _ = env
        _mem(engine, "m-src", "来源记忆")
        _mem(engine, "m-tgt", "目标记忆")
        _fact(engine, "f-src", "来源记忆里的事实", "m-src")
        app.dependency_overrides[get_current_user] = lambda: _principal()
        try:
            with TestClient(app) as c:
                r = c.post("/terminal/merge", json={"source_id": "m-src", "target_id": "m-tgt"})
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 200, r.text
        assert _facts_of(engine, "m-src") == []
        assert [f.id for f in _facts_of(engine, "m-tgt")] == ["f-src"]
