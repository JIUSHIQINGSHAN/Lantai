"""F5 & DD-04 契约与回填验证：

1. 验证开启与关闭 rerank 时，返回结构统一包含 score / memory / document。
2. 验证当有多条 content 相同（不同 id/lane）的候选时，rerank 按 index 正确回填对应 memory 对象，
   避免 doc_to_m = {m.content: m} 正文反查覆盖撞键。
"""
import pytest
from unittest.mock import patch, MagicMock

from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search, RetrievalParams
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
    fake_candidates = [(0.8, mem1), (0.7, mem2)]

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

    class FakeSession:
        def exec(self, stmt, *a, **kw):
            class _Result:
                def __init__(self, data):
                    self.data = data
                def all(self):
                    return self.data
            stmt_str = str(stmt).lower()
            if "memory_edge" in stmt_str or "memoryedge" in stmt_str:
                return _Result([])
            return _Result([mem1, mem2])

    monkeypatch.setattr("lantai.storage.db.get_session", lambda: MagicMock(__enter__=lambda s: FakeSession(), __exit__=lambda *a: None))
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
