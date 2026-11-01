"""兰台巡检 2026-11-01：v0.65 三处回归的修复与回归测试。

1. hybrid 无预算时仍传 timeout=None 给 embed，单参的评测/测试替身 TypeError，
   被 except 吞掉后静默退化成纯关键词检索。
2. 预算模式（D-04）下 embed 仍被 tenacity 重试 3 次、SDK 再各重试 2 次；
   rerank 仍「请求 + sleep 1s + 再请求」。两者都会拖过 2s 帧预算。
3. X-09 密钥防泄漏写死了 api.openai.com：OPENAI_BASE_URL 指向硅基流（.env.example
   的推荐配置）时，精排被静默关掉。

只替身外网（OpenAI 客户端 / requests.post）。
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# 收集期（任何 fixture 之前）拿到真实 embed：conftest 的 autouse fixture 会把
# lantai.llm.client.embed 换成本地替身。
from lantai.llm.client import embed as _real_embed


class _Boom(Exception):
    pass


# ── 1. hybrid 无预算时按旧签名调 embed（param_env 已把向量库换成空替身） ─────────────────────────────────────────


def test_hybrid_without_budget_calls_single_arg_embed(param_env, monkeypatch):
    import lantai.retrieval.hybrid as hybrid

    calls = []

    def single_arg_embed(texts):  # 评测替身 embed_for_eval 的旧签名
        calls.append(list(texts))
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(hybrid, "embed", single_arg_embed)
    hybrid.hybrid_search("我平时喝什么茶", top_k=3)
    assert calls == [["我平时喝什么茶"]]


def test_hybrid_with_budget_passes_timeout(param_env, monkeypatch):
    import lantai.retrieval.hybrid as hybrid

    seen = {}

    def budget_embed(texts, timeout=None):
        seen["timeout"] = timeout
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(hybrid, "embed", budget_embed)
    hybrid.hybrid_search("我平时喝什么茶", top_k=3, budget_s=1.2)
    assert seen["timeout"] == pytest.approx(1.2)


def test_eval_embed_accepts_timeout():
    from lantai.eval.deterministic_embed import embed_for_eval

    assert embed_for_eval(["abc兰台"], timeout=0.5) == embed_for_eval(["abc兰台"])


# ── 2. 预算模式不重试 ──────────────────────────────────────────────────────────────


@pytest.fixture()
def failing_openai(monkeypatch):
    import lantai.llm.client as c

    calls = []
    fake = MagicMock()

    def boom(**kw):
        calls.append(kw)
        raise _Boom("simulated embedding outage")

    fake.embeddings.create.side_effect = boom
    fake.with_options.return_value = fake
    monkeypatch.setattr(c, "get_client", lambda: fake)
    return c, fake, calls


def test_embed_budget_mode_is_single_attempt(failing_openai):
    c, fake, calls = failing_openai
    t = time.perf_counter()
    with pytest.raises(_Boom):
        _real_embed(["x"], timeout=0.1)
    assert len(calls) == 1
    assert time.perf_counter() - t < 0.5  # 改前：3 次请求、约 3.5s
    fake.with_options.assert_called_once_with(timeout=0.1, max_retries=0)


def test_embed_without_budget_keeps_three_retries(failing_openai, monkeypatch):
    from tenacity import wait_none

    c, fake, calls = failing_openai
    monkeypatch.setattr(c._embed_with_retry.retry, "wait", wait_none())
    with pytest.raises(Exception):  # noqa: B017  tenacity 耗尽后抛 RetryError
        _real_embed(["x"])
    assert len(calls) == 3
    fake.with_options.assert_not_called()


@pytest.fixture()
def rerank_on(monkeypatch):
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_API_KEY", "sk-rerank")
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "RERANKER_RETRY_DELAY", 1.0)
    return settings


def test_rerank_budget_mode_single_attempt_no_sleep(rerank_on, monkeypatch):
    import lantai.retrieval.reranker as rr

    posts, sleeps = [], []

    def fake_post(url, json, headers, timeout):
        posts.append(timeout)
        raise _Boom()

    monkeypatch.setattr(rr.requests, "post", fake_post)
    monkeypatch.setattr(rr.time, "sleep", lambda s: sleeps.append(s))
    assert rr.rerank("q", ["a", "b"], top_k=2, timeout=0.8) == []
    assert posts == [0.8]
    assert sleeps == []


def test_rerank_without_budget_keeps_one_retry(rerank_on, monkeypatch):
    import lantai.retrieval.reranker as rr

    posts, sleeps = [], []

    def fake_post(url, json, headers, timeout):
        posts.append(timeout)
        raise _Boom()

    monkeypatch.setattr(rr.requests, "post", fake_post)
    monkeypatch.setattr(rr.time, "sleep", lambda s: sleeps.append(s))
    assert rr.rerank("q", ["a", "b"], top_k=2) == []
    assert posts == [rerank_on.RERANKER_TIMEOUT] * 2
    assert sleeps == [1.0]


# ── 3. X-09：同一服务商按 OPENAI_BASE_URL 的实际 host 判定 ──────────────────────────


class _OkResp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"results": [{"index": 0, "relevance_score": 0.9}]}


def test_siliconflow_gateway_key_reused_for_siliconflow_rerank(monkeypatch):
    """.env.example 配置：OPENAI_BASE_URL 就是硅基流，密钥本来就属于硅基流。"""
    import lantai.retrieval.reranker as rr
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_API_KEY", "")
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-siliconflow-gateway")

    seen = {}

    def fake_post(url, json, headers, timeout):
        seen["auth"] = headers["Authorization"]
        return _OkResp()

    monkeypatch.setattr(rr.requests, "post", fake_post)
    out = rr.rerank("q", ["a"], top_k=1)
    assert seen["auth"] == "Bearer sk-siliconflow-gateway"
    assert len(out) == 1


def test_openai_key_still_not_sent_to_siliconflow(monkeypatch):
    import lantai.retrieval.reranker as rr
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "RERANKER_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKER_API_KEY", "")
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")

    posts = []
    monkeypatch.setattr(rr.requests, "post", lambda *a, **k: posts.append(1))
    assert rr.rerank("q", ["a"], top_k=1) == []
    assert posts == []
