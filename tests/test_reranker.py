"""Reranker 和意图分类单元测试"""

import logging
from unittest.mock import MagicMock, patch

from lantai.retrieval.intent import classify_intent
from lantai.retrieval.reranker import _parse_response, rerank


class TestRerank:
    """硅基流 Reranker 测试"""

    @patch("lantai.retrieval.reranker.requests.post")
    def test_rerank_success(self, mock_post):
        """正常响应"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": "rerank-xxx",
            "results": [
                {"index": 1, "score": 0.95, "document": "banana"},
                {"index": 0, "score": 0.87, "document": "apple"},
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = rerank("query", ["apple", "banana", "fruit"], top_k=2)
        assert len(result) == 2
        assert result[0]["score"] == 0.95
        assert result[0]["document"] == "banana"

    @patch("lantai.retrieval.reranker.requests.post")
    def test_rerank_retry_then_success(self, mock_post):
        """第一次失败，重试成功"""
        mock_resp_ok = MagicMock()
        mock_resp_ok.json.return_value = {
            "results": [{"index": 0, "score": 0.8, "document": "apple"}]
        }
        mock_resp_ok.raise_for_status = MagicMock()
        mock_post.side_effect = [Exception("timeout"), mock_resp_ok]

        result = rerank("query", ["apple"], top_k=1)
        assert len(result) == 1
        assert mock_post.call_count == 2

    @patch("lantai.retrieval.reranker.requests.post")
    def test_rerank_all_fail_returns_empty(self, mock_post):
        """两次都失败，返回空列表"""
        mock_post.side_effect = [Exception("timeout"), Exception("timeout")]

        result = rerank("query", ["apple"], top_k=1)
        assert result == []

    def test_rerank_empty_docs(self):
        """空文档列表直接返回空"""
        result = rerank("query", [], top_k=5)
        assert result == []


class TestParseResponseIndexGuard:
    """审查 M11（票 15）：缺失/越界 index 的返回项跳过并留痕，不默认 0 脏配候选。

    直调 _parse_response 的不 mock 冒烟：真实构造替身响应，不替换任何内部逻辑。
    """

    def test_missing_index_skipped_and_logged(self, caplog):
        """缺 index 的返回项跳过（不默认 0），warning 留痕"""
        raw = {"results": [{"score": 0.99, "document": "doc B"}]}
        with caplog.at_level(logging.WARNING, logger="lantai"):
            parsed = _parse_response(raw, ["doc A", "doc B"], top_k=1)
        assert parsed == []
        assert any("missing or out-of-range index" in r.getMessage() for r in caplog.records)

    def test_out_of_range_index_skipped(self, caplog):
        """越界 index 的返回项跳过，warning 留痕"""
        raw = {"results": [{"index": 7, "score": 0.9, "document": "doc X"}]}
        with caplog.at_level(logging.WARNING, logger="lantai"):
            parsed = _parse_response(raw, ["doc A", "doc B"], top_k=1)
        assert parsed == []
        assert any("missing or out-of-range index" in r.getMessage() for r in caplog.records)

    def test_negative_index_skipped(self):
        """负数 index 越界，同样跳过"""
        raw = {"results": [{"index": -1, "score": 0.9, "document": "doc B"}]}
        parsed = _parse_response(raw, ["doc A", "doc B"], top_k=1)
        assert parsed == []

    def test_partial_missing_index_keeps_valid_item(self):
        """部分缺失：合法项保留且映射正确，缺失项跳过不脏配"""
        raw = {
            "results": [
                {"score": 0.99, "document": "doc B"},
                {"index": 1, "score": 0.9, "document": "doc B"},
            ]
        }
        parsed = _parse_response(raw, ["doc A", "doc B"], top_k=2)
        assert len(parsed) == 1
        assert parsed[0] == {"score": 0.9, "document": "doc B", "index": 1}

    def test_normal_response_unchanged(self):
        """正常响应行为不变（含缺 document 回退原文的既有路径）"""
        raw = {
            "results": [
                {"index": 1, "score": 0.95, "document": "banana"},
                {"index": 0, "score": 0.87},
            ]
        }
        parsed = _parse_response(raw, ["apple", "banana", "fruit"], top_k=2)
        assert parsed == [
            {"score": 0.95, "document": "banana", "index": 1},
            {"score": 0.87, "document": "apple", "index": 0},
        ]


class TestIntent:
    """意图分类测试"""

    @patch("lantai.retrieval.intent.chat_json")
    def test_classify_fact_lookup(self, mock_chat):
        mock_chat.return_value = {"intent": "fact_lookup", "reason": "short query"}
        result = classify_intent("什么是记忆系统")
        assert result["intent"] == "fact_lookup"
        assert result["candidate_n"] == 10

    @patch("lantai.retrieval.intent.chat_json")
    def test_classify_procedural(self, mock_chat):
        mock_chat.return_value = {"intent": "procedural", "reason": "how-to"}
        result = classify_intent("怎么做记忆迭代")
        assert result["intent"] == "procedural"
        assert result["candidate_n"] == 15

    @patch("lantai.retrieval.intent.chat_json")
    def test_classify_exploratory(self, mock_chat):
        mock_chat.return_value = {"intent": "exploratory", "reason": "broad"}
        result = classify_intent("记忆系统的各种设计模式对比")
        assert result["intent"] == "exploratory"
        assert result["candidate_n"] == 20

    @patch("lantai.retrieval.intent.chat_json")
    def test_classify_fallback_on_error(self, mock_chat):
        mock_chat.side_effect = Exception("LLM timeout")
        result = classify_intent("测试")
        assert result["intent"] == "fact_lookup"
