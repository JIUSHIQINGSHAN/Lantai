"""Reranker 和意图分类单元测试"""
from unittest.mock import MagicMock, patch

from lantai.retrieval.intent import classify_intent
from lantai.retrieval.reranker import rerank


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
            ]
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
