"""Raw Drawer 原文直存（P0-1）核心函数冒烟测试（不 mock 内部逻辑）。

测试纪律：mock 仅用于外部依赖（embedding 网络、向量存储、意图 LLM）；
add_raw_memory 的产品代码（SQLite 写入 / FTS 同步 / 幂等去重）真实执行。
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.text import normalize_for_index
from lantai.models.schemas import RawMemoryReq
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts


@pytest.fixture()
def raw_env():
    """内存 SQLite 真实建表 + FTS 初始化 + patch 仅外部依赖。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.services.memory_service.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
    ):
        yield session_factory, engine, vector_store_mock


def _count_fts(engine, memory_id: str) -> int:
    conn = engine.raw_connection()
    try:
        return len(
            conn.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_id = ?", (memory_id,)
            ).fetchall()
        )
    finally:
        conn.close()


def test_build_verbatim_item_smoke():
    """构造纯函数不 mock：sha256 幂等 key + 固定语义字段 + 时间戳显式/缺省。"""
    from datetime import datetime

    from lantai.services.memory_service import build_verbatim_item

    item = build_verbatim_item(
        "配置备份脚本 backup.sh",
        "fact",
        ["旧"],
        created_at=datetime(2026, 1, 2, 3, 4, 5),
    )
    assert item.memory_type == "verbatim"
    assert item.lane == "fact"
    assert item.tags == ["旧"]
    assert item.tier == "long_term"
    assert item.confidence == 1.0
    assert item.created_at == datetime(2026, 1, 2, 3, 4, 5)
    assert item.updated_at == item.created_at  # updated_at 缺省取 created_at
    # 内容 sha256 幂等 key：同内容两次构造 key 一致
    assert build_verbatim_item("配置备份脚本 backup.sh", "fact").key == item.key
    # 缺省时间戳路径不炸（utcnow）
    assert build_verbatim_item("x", "general").created_at is not None


def test_add_raw_writes_verbatim_and_indexes(raw_env):
    """真实 DB 写入：memory_type=verbatim + FTS 索引行存在 + 检索命中。"""
    session_factory, engine, _ = raw_env
    from lantai.services.memory_service import add_raw_memory

    content = "docker run -p 8080:80 nginx:latest"
    result = add_raw_memory(RawMemoryReq(content=content, title="deploy cmd", lane="fact"))
    assert result["dedup"] is False
    assert result["verbatim"] is True

    mid = result["memory_id"]
    with session_factory() as s:
        m = s.get(MemoryItem, mid)
        assert m is not None
        assert m.memory_type == "verbatim"
        assert m.status == "active"
        assert m.tier == "long_term"
        assert m.lane == "fact"
    assert _count_fts(engine, mid) == 1


def test_add_raw_dedup_idempotent(raw_env):
    """重复内容幂等：返回同一 memory_id，不重复写。"""
    session_factory, engine, _ = raw_env
    from lantai.services.memory_service import add_raw_memory

    r1 = add_raw_memory(RawMemoryReq(content="配置项 A=1"))
    r2 = add_raw_memory(RawMemoryReq(content="配置项 A=1"))
    assert r1["memory_id"] == r2["memory_id"]
    assert r2["dedup"] is True
    with session_factory() as s:
        rows = s.exec(select(MemoryItem).where(MemoryItem.memory_type == "verbatim")).all()
        assert len(rows) == 1


def test_add_raw_zero_llm(raw_env):
    """零 LLM：提取器绝不执行（若被调用则抛 AssertionError）。"""
    session_factory, engine, _ = raw_env
    from lantai.services.memory_service import add_raw_memory

    with patch(
        "lantai.services.memory_service.extract_candidate",
        side_effect=AssertionError("LLM extractor must not run for verbatim"),
    ):
        result = add_raw_memory(RawMemoryReq(content="长日志片段 <error code=500> stacktrace"))
    assert result["memory_id"]


def test_add_raw_searchable_via_fts_fallback(raw_env):
    """检索自动命中：向量不可用时 FTS 兜底路径能召回 verbatim 原文。"""
    session_factory, engine, _ = raw_env
    from lantai.retrieval.hybrid import hybrid_search
    from lantai.services.memory_service import add_raw_memory

    content = "系统部署手册：备份命令 mysqldump -u root db > backup.sql"
    add_raw_memory(RawMemoryReq(content=content))

    results = hybrid_search(
        "mysqldump 备份命令", top_k=5, use_rerank=False, memory_types=["verbatim"]
    )
    texts = [r.get("memory", {}).get("content", "") for r in results]
    assert any(content in t for t in texts)


# ------------------------------------------------------------------ 票 24 子项 2：并发同文双行


class TestRawAddConcurrentDuplicate:
    """票 24 子项 2：/memory/add/raw 的去重是「SELECT(key=sha256) 判重 → embed
    （网络往返）→ INSERT」——MemoryItem.key 无唯一约束兜底，并发同文两路都
    判重落空 → 两行 verbatim + 双份向量/FTS。

    交错缝：embed（外部 LLM 边界，A 首条 DML 之前——SELECT 判重不含任何写）。
    垫片先调真（替身）实现拿到向量，再在返回前让 B 走完整真实 add_raw_memory
    （同 StaticPool 连接、真实提交）。单线程顺序执行，确定性复现，无 sleep。
    修复语义：(memory_type, key, user_id) 部分唯一索引（WHERE key != '' AND
    status='active'，与去重 SELECT 的口径逐字对齐）+ 败方撞约束重读收敛。
    """

    CONTENT = "票 24 并发同文直存：完全相同的一段原文"

    def test_concurrent_same_content_single_row(self, raw_env):
        import hashlib

        import lantai.services.memory_service as ms_mod
        from lantai.services.memory_service import add_raw_memory

        session_factory, engine, vector_store_mock = raw_env
        # 票 46：身份键过归一缝（本 CONTENT 含全角冒号，归一 sha ≠ 原文 sha）——
        # 单行语义按新旧双键口径查，key 形态本身不是本锁的对象。
        h = hashlib.sha256(self.CONTENT.encode("utf-8")).hexdigest()
        h_norm = hashlib.sha256(normalize_for_index(self.CONTENT).encode("utf-8")).hexdigest()

        racing = {"done": False}
        real_embed = ms_mod.embed  # fixture 替身（假 8 维向量）
        b_result: dict = {}

        def _embed_with_interleave(texts):
            out = real_embed(texts)
            if racing["done"]:
                return out
            racing["done"] = True
            b_result.update(add_raw_memory(RawMemoryReq(content=self.CONTENT), user_id="default"))
            assert b_result.get("memory_id"), f"B 侧直存失败：{b_result}"
            return out

        with patch("lantai.services.memory_service.embed", side_effect=_embed_with_interleave):
            out_a = add_raw_memory(RawMemoryReq(content=self.CONTENT), user_id="default")

        assert b_result, "交错未发生：B 侧未执行（缝位失配，测试失效）"
        with session_factory() as s:
            rows = s.exec(
                select(MemoryItem).where(
                    MemoryItem.memory_type == "verbatim", MemoryItem.key.in_([h, h_norm])
                )
            ).all()
        assert len(rows) == 1, (
            f"并发同文 {len(rows)} 行 verbatim（票 24 子项 2）：SELECT 判重后含网络往返、"
            "无唯一约束兜底，双行 + 双份向量/FTS"
        )
        assert vector_store_mock.add.call_count == 1, (
            f"向量写 {vector_store_mock.add.call_count} 次（票 24 子项 2）：并发双行伴生双份向量"
        )
        # 败方收敛到胜者那行（幂等回执形状与串行去重命中一致）
        assert out_a.get("memory_id") == b_result["memory_id"] or out_a.get("dedup") is True, (
            f"A 侧未收敛：a={out_a} b={b_result}"
        )
