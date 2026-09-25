"""E2 双视图评测（票 11）验收测试：≥30 条用例、正确率 ≥90%（roadmap P1-1 口径）。

不 mock：真实内存库 + hybrid_search 全链（embed 确定性替身 + 内嵌 Chroma）。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.evolution.promoter as promoter_mod
import lantai.retrieval.hybrid as hybrid_mod
import lantai.storage.db as db_module
import lantai.storage.vector_store as vs_module
from lantai.storage.fts import init_fts


def _hash_embed(texts):
    out = []
    for text in texts:
        v = [0.0] * 512
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))] or [text]
        for g in grams:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % 512] += 1.0
        n = sum(v) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture()
def e2_env(tmp_path, monkeypatch):
    """模块级环境（同一内存库跑全部用例，embed 一致性保证召回稳定）。"""
    import lantai.eval.models  # noqa: F401
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid_mod, "embed", _hash_embed)
    monkeypatch.setattr(promoter_mod, "embed", _hash_embed)

    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma-e2"))
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(vs_module, "_store", None, raising=False)

    yield engine

    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    engine.dispose()


def test_case_count_at_least_30(e2_env):
    """roadmap 口径：30+ 时间/更新用例。"""
    from lantai.eval.e2_temporal_cases import build_cases

    assert len(build_cases()) >= 30


def test_dual_view_accuracy_at_least_90(e2_env):
    """roadmap 口径：当前/历史证据选择正确率 ≥90%（实测留痕）。"""
    from lantai.eval.e2_temporal_cases import run_e2_temporal_eval

    result = run_e2_temporal_eval()
    print(
        f"\n[E2 实测] queries={result['queries']} correct={result['correct']} "
        f"accuracy={result['accuracy']}"
    )
    for f in result["failures"][:5]:
        print("[E2 failure]", f)
    assert result["queries"] >= 30
    assert result["accuracy"] is not None and result["accuracy"] >= 0.9


def test_mcp_search_temporal_passthrough(e2_env, monkeypatch):
    """票 11 冒烟：MCP search 工具透传 as_of/time_from/time_to（真实 handle_search 直调）。"""
    from lantai.eval.e2_temporal_cases import build_seed_items
    from lantai.retrieval.hybrid import index_memory_item
    from lantai.storage.fts import sync_fts
    from lantai.cli.mcp import handle_search

    with db_module.get_session() as s:
        for item in build_seed_items():
            existing = s.get(__import__("lantai.models.tables", fromlist=["MemoryItem"]).MemoryItem, item.id)
            if existing:
                s.delete(existing)
        s.commit()
        for item in build_seed_items():
            s.add(item)
            s.commit()
            sync_fts(s, item.id, item.content)
    for item in build_seed_items():
        index_memory_item(item.id, _hash_embed([item.content])[0], {"memory_id": item.id})

    # 合法透传：不炸且有结构化返回
    res = handle_search(
        {"query": "用户的主数据库是什么", "top_k": 5, "as_of": "2026-03-15T00:00:00+00:00"}
    )
    assert "results" in res
    # 非法 as_of（非字符串）→ ValueError（fail-closed，不静默）
    import pytest as _pytest

    with _pytest.raises(ValueError):
        handle_search({"query": "x" * 10, "as_of": 12345})
