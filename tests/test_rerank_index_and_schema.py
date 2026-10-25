"""F5 & DD-04 契约与回填验证：

1. 验证开启与关闭 rerank 时，返回结构统一包含 score / memory / document。
2. 验证当有多条 content 相同（不同 id/lane）的候选时，rerank 按 index 正确回填对应 memory 对象，
   避免 doc_to_m = {m.content: m} 正文反查覆盖撞键。
3. 审查 M11（票 15）：替身响应缺 index → 候选分数不被错配（不默认 0 脏配），
   全缺失走既有降级链（拾遗 ADR-0028 口径），诊断面有留痕。
"""

import logging
from unittest.mock import MagicMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import RetrievalParams, hybrid_search
from lantai.retrieval.reranker import _parse_response


def test_parse_response_retains_index():
    """验证 _parse_response 保留 index 字段。"""
    raw = {
        "results": [
            {"index": 2, "score": 0.9, "document": "doc C"},
            {"index": 0, "score": 0.8, "document": "doc A"},
        ]
    }
    parsed = _parse_response(raw, ["doc A", "doc B", "doc C"], top_k=2)
    assert len(parsed) == 2
    assert parsed[0]["index"] == 2
    assert parsed[0]["score"] == 0.9
    assert parsed[1]["index"] == 0
    assert parsed[1]["score"] == 0.8


def test_rerank_index_backfill_handles_duplicate_content(monkeypatch):
    """验证相同正文的记忆在 rerank 结果中按 index 正确对应，不发生字典覆写。"""
    # 构造两条正文完全一样但 id、lane 不同的记忆
    mem1 = MemoryItem(id="mem_1", content="会议定在下周一召开", lane="work", status="active")
    mem2 = MemoryItem(id="mem_2", content="会议定在下周一召开", lane="chat", status="active")

    # 模拟 candidates

    # 模拟 rerank 返回反向排序：第 1 项（mem2）得分更高，第 0 项（mem1）得分较低
    fake_rerank_output = [
        {"score": 0.95, "document": "会议定在下周一召开", "index": 1},
        {"score": 0.85, "document": "会议定在下周一召开", "index": 0},
    ]

    monkeypatch.setattr("lantai.retrieval.hybrid.classify_intent", lambda q: {"candidate_n": 2})
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", lambda q: [[0.1] * 768])

    class FakeVectorStore:
        def search(self, *a, **kw):
            return [{"id": "mem_1", "distance": 0.2}, {"id": "mem_2", "distance": 0.3}]

    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: FakeVectorStore())

    # 真实内存 SQLite：记忆与 supersedes 边查询都走真库（无会话替身）
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(mem1)
        s.add(mem2)
        s.commit()
    monkeypatch.setattr("lantai.storage.db.get_session", lambda: Session(engine))
    monkeypatch.setattr("lantai.retrieval.hybrid.rerank", lambda q, docs, k: fake_rerank_output)

    # 显式开启 rerank
    params = RetrievalParams.from_overrides({"RERANKER_ENABLED": True})
    results = hybrid_search("开会", top_k=2, use_rerank=True, params=params)

    assert len(results) == 2
    # 第一项应该是 index=1 对应的 mem2
    assert results[0]["memory"]["id"] == "mem_2"
    assert results[0]["score"] == 0.95
    assert results[0]["document"] == "会议定在下周一召开"

    # 第二项应该是 index=0 对应的 mem1
    assert results[1]["memory"]["id"] == "mem_1"
    assert results[1]["score"] == 0.85
    assert results[1]["document"] == "会议定在下周一召开"


@patch("lantai.retrieval.reranker.requests.post")
def test_rerank_missing_index_degrades_not_misassigned(mock_post, monkeypatch, caplog):
    """票 15 不 mock 冒烟：替身响应缺 index → 第 0 号候选分数不被错配。

    仅 mock 外部网络（requests.post）；rerank() → _parse_response → hybrid
    匹配/降级链全部真实执行。修复前：缺 index 被默认 0，唯一返回项的
    0.999 错配给第 0 号候选且只剩 1 条结果；修复后：该项被跳过并留痕，
    全缺失走既有降级链回退混合检索分数。
    """
    mem1 = MemoryItem(id="mem_1", content="甲案数据都在内网文档库", lane="work", status="active")
    mem2 = MemoryItem(id="mem_2", content="乙案数据都在外网知识库", lane="chat", status="active")

    # 替身响应：唯一返回项缺 index，score 0.999 若被脏配会顶到第 0 号候选头上
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [{"score": 0.999, "document": "乙案数据都在外网知识库"}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    monkeypatch.setattr("lantai.retrieval.hybrid.classify_intent", lambda q: {"candidate_n": 2})
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", lambda q: [[0.1] * 768])

    class FakeVectorStore:
        def search(self, *a, **kw):
            return [{"id": "mem_1", "distance": 0.2}, {"id": "mem_2", "distance": 0.3}]

    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: FakeVectorStore())

    # 真实内存 SQLite：记忆与召回查询都走真库
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(mem1)
        s.add(mem2)
        s.commit()
    monkeypatch.setattr("lantai.storage.db.get_session", lambda: Session(engine))

    params = RetrievalParams.from_overrides({"RERANKER_ENABLED": True})
    with caplog.at_level(logging.WARNING, logger="lantai"):
        results = hybrid_search("数据在哪", top_k=2, use_rerank=True, params=params)

    # 不脏配：0.999 不出现在任何结果里（修复前第 0 号被错配 0.999）
    assert all(r["score"] != 0.999 for r in results)
    # 全缺失 → 既有降级链：两条候选按混合检索分原样返回
    assert len(results) == 2
    assert {r["memory"]["id"] for r in results} == {"mem_1", "mem_2"}
    # 诊断面留痕：warning 记录跳过计数
    assert any("missing or out-of-range index" in r.getMessage() for r in caplog.records)
