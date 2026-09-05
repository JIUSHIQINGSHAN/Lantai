"""Ticket 04: Search Transparency——build_evidence 核心冒烟测试。

从检索结果提取"来源说明"（id + 内容摘要 + 分数）：
- 非 rerank 结构：{"score", "memory": {id, content}}
- rerank 结构：{"score", "document"} → 反查 DB 拿 id（查不到则 id=None）
- 空输入 → 空列表；异常静默降级
"""

from lantai.models.tables import MemoryItem


class TestBuildEvidence:
    def test_memory_item_structure(self, param_env):
        """非 rerank 结果 → 直接取 id + content[:200] + score"""
        from lantai.retrieval.evidence import build_evidence
        results = [{"score": 0.9, "memory": {"id": "mem_1",
                                             "content": "用户喜欢 Python 和 Rust"}}]
        ev = build_evidence(results)
        assert ev == [{"id": "mem_1", "content": "用户喜欢 Python 和 Rust",
                       "score": 0.9}]

    def test_rerank_document_resolves_id(self, param_env):
        """rerank 结果（仅 document）→ 从 DB 反查记忆 id"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(MemoryItem(id="mem_1", memory_type="semantic", key="k",
                             content="用户喜欢 Python 和 Rust"))
            s.commit()

        from lantai.retrieval.evidence import build_evidence
        results = [{"score": 0.85, "document": "用户喜欢 Python 和 Rust"}]
        ev = build_evidence(results)
        assert ev[0]["id"] == "mem_1"
        assert ev[0]["content"] == "用户喜欢 Python 和 Rust"
        assert ev[0]["score"] == 0.85

    def test_rerank_document_missing_id_none(self, param_env):
        """反查不到（记忆已删）→ id=None，内容摘要仍给"""
        from lantai.retrieval.evidence import build_evidence
        results = [{"score": 0.5, "document": "不存在的记忆内容"}]
        ev = build_evidence(results)
        assert ev[0]["id"] is None
        assert ev[0]["content"] == "不存在的记忆内容"

    def test_empty_input(self, param_env):
        from lantai.retrieval.evidence import build_evidence
        assert build_evidence([]) == []

    def test_content_truncated(self, param_env):
        from lantai.retrieval.evidence import build_evidence
        long_text = "字" * 500
        results = [{"score": 1.0, "memory": {"id": "mem_1", "content": long_text}}]
        ev = build_evidence(results)
        assert len(ev[0]["content"]) == 200
