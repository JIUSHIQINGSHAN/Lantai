"""守护锁：`/cognitive/observe` 写进来的记忆必须能被检索到。

修前该端点只 `db.add` + `commit`，既不 `sync_fts` 也不 `index_memory_item`——
观测记忆对 FTS 与向量两条召回通道永久不可见，只有向量失败时的 LIKE 兜底
偶尔命中。用户写进去、检索搜不到，且没有任何错误提示。

本锁走**真实端点 + 真实 FTS**：POST 之后直接用 `search_fts`（裸连接面，
不 mock）查同一个词。向量面用替身（外部网络 + 外部存储），并在另一条用例里
验证「向量失败不回滚已落库的记忆」。

不 mock 产品逻辑：端点、FTS 表、sync_fts 全是真的。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.storage.fts import init_fts, search_fts

_OBS_TEXT = "观测记录：内网跳板机的部署脚本放在 /opt/deploy 目录"


@pytest.fixture
def observe_env(monkeypatch):
    from lantai.api import routes_cognitive as routes_cognitive_mod
    from lantai.api.app import app
    from lantai.storage import db as db_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    # 真 FTS 表（不是替身）：本锁要验证的正是「有没有写进 FTS」
    init_fts(engine.raw_connection())

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

    # 向量面替身（外部网络 + 外部存储），并记录调用
    indexed: list[str] = []

    def fake_embed(texts, timeout=None):
        return [[0.1] * 8 for _ in texts]

    def fake_index(memory_id, embedding, metadata):
        indexed.append(memory_id)

    import lantai.llm.client as llm_mod

    monkeypatch.setattr(llm_mod, "embed", fake_embed)

    dep = routes_cognitive_mod.get_session_dep

    def _override():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[dep] = _override
    with TestClient(app) as client:
        yield client, engine, indexed, monkeypatch
    app.dependency_overrides.pop(dep, None)


def _post_observe(client):
    return client.post(
        "/cognitive/observe",
        json={"content": _OBS_TEXT, "evidence_type": "observation", "reliability": 0.8},
    )


def test_observed_memory_is_fts_searchable(observe_env):
    """POST 之后，同一段正文必须能被 FTS 通道召回（修前零命中）。"""
    client, engine, _indexed, _mp = observe_env
    resp = _post_observe(client)
    assert resp.status_code == 200, resp.text
    mem_id = resp.json()["memory_id"]

    with Session(engine) as s:
        conn = s.connection().connection.driver_connection
        # 查询词要取正文里**连续**的一段：`search_fts` 是 3-gram AND 语义
        # （每个 gram 都要命中），跨词拼接的查询会自己零命中，与本锁无关。
        hits = search_fts(conn, "部署脚本", top_k=10)
    assert mem_id in hits, f"观测记忆没进 FTS，检索不可见：{hits}"


def test_observed_memory_is_vector_indexed(observe_env, monkeypatch):
    """向量面也要建索引（两侧都建才算「可被检索」）。"""
    client, _engine, _indexed, _mp = observe_env
    from lantai.services import memory_service  # noqa: F401  确保模块已载

    calls: list[str] = []

    def counting_index(memory_id, embedding, metadata):
        calls.append(memory_id)

    import lantai.retrieval.hybrid as hybrid_mod

    monkeypatch.setattr(hybrid_mod, "index_memory_item", counting_index)
    resp = _post_observe(client)
    assert resp.status_code == 200, resp.text
    assert calls == [resp.json()["memory_id"]], f"向量索引未调用：{calls}"


def test_vector_failure_keeps_memory_and_reports(observe_env, monkeypatch):
    """向量索引失败不得回滚已落库的记忆，且要如实回报 warning。"""
    client, engine, _indexed, _mp = observe_env
    from sqlalchemy import select

    import lantai.retrieval.hybrid as hybrid_mod
    from lantai.models.tables import MemoryItem

    def boom(*a, **k):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(hybrid_mod, "index_memory_item", boom)
    resp = _post_observe(client)
    assert resp.status_code == 200, f"向量失败不该让写入失败：{resp.text}"
    payload = resp.json()
    assert payload.get("warning") == "vector_index_failed"

    mem_id = payload["memory_id"]
    with Session(engine) as s:
        row = s.exec(select(MemoryItem).where(MemoryItem.id == mem_id)).first()
    assert row is not None, "向量失败把记忆一起回滚了"


def test_fts_unavailable_does_not_500(observe_env, monkeypatch):
    """FTS 初始化判不可用时，写入仍要成功（降级但可用，不 500）。"""
    client, _engine, _indexed, _mp = observe_env
    from lantai.storage import db as db_module

    monkeypatch.setattr(db_module, "FTS_OK", False)
    resp = _post_observe(client)
    assert resp.status_code == 200, f"FTS 不可用把写入打成了错误：{resp.text}"
    assert resp.json()["ok"] is True
