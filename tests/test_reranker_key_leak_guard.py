"""X-09 跨服务商密钥泄露防护回归测试（票 .scratch/v0.65-next-window-2026-11/04）。

验证：
1. 当 RERANKER_API_KEY 未配且 host 为默认的 siliconflow (或其他非 openai 域名) 时，严禁回退 OPENAI_API_KEY 并直接安全降级；
2. 当 host 为 api.openai.com 时，保留向后兼容的 OPENAI_API_KEY 回退能力；
3. 当 RERANKER_API_KEY 明确配置时，正常放行。
"""

from unittest.mock import patch
import pytest

from lantai.retrieval.reranker import rerank
from lantai.core.settings import settings


def test_siliconflow_host_does_not_leak_openai_key(monkeypatch):
    """当重排指向 SiliconFlow 时，严防将 OPENAI_API_KEY 作为 Bearer 传出。"""
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "RERANKER_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "dummy-test-mock-key-only")

    with patch("requests.post") as mock_post:
        res = rerank("测试查询", ["文档 1", "文档 2"], top_k=2)

    # 必须提前短路返回空列表，绝对不发起任何外发 HTTP 请求
    assert res == []
    mock_post.assert_not_called()


def test_openai_host_allows_openai_key_fallback(monkeypatch):
    """当重排 host 同为 api.openai.com 时，允许正常回退复用 OPENAI_API_KEY。"""
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "RERANKER_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "dummy-test-mock-key-only")

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "results": [{"index": 0, "relevance_score": 0.9}]
        }
        mock_post.return_value.raise_for_status = lambda: None

        res = rerank("测试查询", ["文档 1"], top_k=1)

    mock_post.assert_called_once()
    headers = mock_post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer dummy-test-mock-key-only"
    assert len(res) == 1
