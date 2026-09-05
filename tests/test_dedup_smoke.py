"""DD-01 冒烟测试：_apply_dedup 真实链路验证（不 mock vector_store.search）。

背景：v0.21.0 审阅发现 _apply_dedup 将 str 直传 vector_store.search()
（期望 list[float]），导致去重静默失效。此测试用真实临时 ChromaDB 目录
验证 embed → search → find_similar 链路完整性，确保 merge/update 判定正常。

遵循 AGENTS.md 测试纪律：
  "每个核心函数必须至少有一个不 mock 的冒烟测试（真实构造最小输入直调该函数，
   验证主路径不炸）。"
  "mock 允许用于：外部网络（LLM/embedding/rerank）；
   不允许用于：让被测函数跳过其内部计算逻辑。"
"""
import pytest
import tempfile
import shutil
from unittest.mock import patch
from sqlmodel import SQLModel, create_engine, Session
from sqlmodel.pool import StaticPool

from lantai.storage import db
from lantai.models.tables import MemoryItem
from lantai.core.ids import new_id


# 固定 768 维假向量（只 mock embed 网络调用，不 mock search / find_similar）
_FIXED_VECTOR = [0.1] * 768
_SIMILAR_VECTOR = [0.1 + 1e-5] * 768  # 余弦距离极近 → 应触发 merge


@pytest.fixture()
def smoke_env(tmp_path, monkeypatch):
    """构造真实 ChromaDB + 内存 SQLite 环境。"""
    # 内存 SQLite
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.engine = engine
    SQLModel.metadata.create_all(engine)

    # 真实 ChromaDB（临时目录）
    chroma_dir = str(tmp_path / "chroma_smoke")
    monkeypatch.setattr("lantai.core.settings.settings.CHROMADB_PATH", chroma_dir)

    # 清除 vector_store 全局单例缓存，确保用新路径创建
    import lantai.storage.vector_store as vs_mod
    monkeypatch.setattr(vs_mod, "_store", None)

    # 重新创建 vector_store 实例并注入到 memory_service
    from lantai.storage.vector_store import get_vector_store
    fresh_vs = get_vector_store()
    monkeypatch.setattr("lantai.services.memory_service.vector_store", fresh_vs)

    # mock embed（外部网络调用），返回固定向量
    def fake_embed(texts):
        return [_FIXED_VECTOR[:] for _ in texts]

    monkeypatch.setattr("lantai.services.memory_service.embed", fake_embed)

    yield engine, fresh_vs


def test_apply_dedup_merge_smoke(smoke_env):
    """真实链路冒烟：写入一条记忆后，相同内容应触发 merge（非 insert）。

    验证 _apply_dedup 的 embed → vector_store.search → find_similar 链路完整。
    """
    engine, vs = smoke_env

    # Step 1: 向 SQLite 和 ChromaDB 写入一条种子记忆
    with Session(engine) as s:
        mem_id = new_id("mem")
        mem = MemoryItem(
            id=mem_id,
            memory_type="preference",
            key="smoke_coffee",
            content="用户喜欢喝咖啡",
            lane="preference",
            status="active",
            importance=0.5,
        )
        s.add(mem)
        s.commit()

    # 同时索引到 ChromaDB（模拟正常写入路径）
    vs.add(
        ids=[mem_id],
        embeddings=[_FIXED_VECTOR[:]],
        metadatas=[{"memory_type": "preference"}],
    )

    # Step 2: 调用 _apply_dedup（被测核心函数）
    from lantai.services.memory_service import _apply_dedup

    with Session(engine) as s:
        action, target, sim = _apply_dedup(s, "用户喜欢喝咖啡", fastpath=True)

    # Step 3: 断言：应该触发 merge（余弦距离 ≈ 0），而非 insert
    assert action == "merge", (
        f"DD-01 回归：_apply_dedup 应返回 'merge'，实际返回 '{action}'。"
        f"如果返回 'insert'，说明 embed→search 链路断裂。sim={sim}"
    )
    assert target is not None
    assert target.id == mem_id


def test_apply_dedup_insert_for_novel_content(smoke_env):
    """真实链路冒烟：ChromaDB 为空时，应返回 insert。"""
    engine, vs = smoke_env

    from lantai.services.memory_service import _apply_dedup

    with Session(engine) as s:
        action, target, sim = _apply_dedup(s, "全新的从未见过的记忆内容", fastpath=True)

    assert action == "insert", f"空库应返回 'insert'，实际返回 '{action}'"
    assert target is None
