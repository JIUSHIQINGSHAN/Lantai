"""意图分类预算传导（票 90）不 mock 冒烟。

病灶（2026-11-24 真机验收实证）：`hybrid_search` 的 `budget_s` 只传给了
`embed`，`classify_intent` 每轮同步吃一整次 LLM 调用——本机网关实测 11.0s。
钩子 8s 墙下每轮必超时降级成画像兜底，`event_id` 丢失，回执链整条断掉。
实测：`LANTAI_INTENT_OFF=1` 时单帧 3.7–4.9s（正常注入）；开着时 10.5s（降级）。

修法：`classify_intent(query, timeout=...)` 接预算，超预算即抛给自身 except
回落 `DEFAULT_INTENT`（与 embed 预算模式同口径：一次机会即止，不重试）。

不 mock 原则：这里 mock 的是外部网络（LLM 调用延迟），被测函数的
「预算是否真的传到 chat_json」「超时是否真的回落默认意图」都是真实计算。
"""

import time

import pytest

from lantai.core.settings import settings
from lantai.retrieval import hybrid, intent


class _EmptyVectorStore:
    """向量库替身：零召回（外部存储面），让 hybrid 走真实兜底路径。"""

    def search(self, qv, top_k, filters=None):
        return []


def test_intent_budget_propagates_to_chat_json(monkeypatch):
    """预算必须真的传到 chat_json 的 timeout 形参（修复前恒为 None）。"""
    captured = {}

    def fake_chat_json(system, user, timeout=None):
        captured["timeout"] = timeout
        captured["user"] = user
        return {"intent": "procedural", "reason": "含怎么"}

    monkeypatch.setattr(intent, "chat_json", fake_chat_json)
    monkeypatch.delenv("LANTAI_INTENT_OFF", raising=False)

    out = intent.classify_intent("部署怎么做", timeout=1.7)

    assert captured["timeout"] == 1.7, "预算未传导：chat_json 没收到 timeout"
    assert out["intent"] == "procedural"
    assert out["candidate_n"] == int(settings.INTENT_CANDIDATE_SIZES["procedural"])


def test_intent_budget_exceeded_falls_back_to_default(monkeypatch):
    """LLM 在预算内失败 → 回落 DEFAULT_INTENT，不抛异常、不阻塞调用方。"""

    def slow_chat_json(system, user, timeout=None):
        # 复刻预算模式语义：一次机会睡满预算即抛，不重试
        time.sleep(min(0.3, timeout) if timeout is not None else 0.3)
        raise TimeoutError("budget exceeded")

    monkeypatch.setattr(intent, "chat_json", slow_chat_json)
    monkeypatch.delenv("LANTAI_INTENT_OFF", raising=False)

    start = time.perf_counter()
    out = intent.classify_intent("部署怎么做", timeout=0.2)
    elapsed = time.perf_counter() - start

    assert out["intent"] == settings.DEFAULT_INTENT
    assert out["candidate_n"] == settings.INTENT_CANDIDATE_SIZES[settings.DEFAULT_INTENT]
    assert elapsed < 2.0, f"超预算回落耗时 {elapsed:.2f}s 过长"


def test_intent_off_switch_bypasses_llm(monkeypatch):
    """LANTAI_INTENT_OFF=1 走默认意图，且不触网（既有开关零回归）。"""

    def boom(*a, **kw):
        raise AssertionError("LANTAI_INTENT_OFF=1 时不该调用 LLM")

    monkeypatch.setattr(intent, "chat_json", boom)
    monkeypatch.setenv("LANTAI_INTENT_OFF", "1")

    out = intent.classify_intent("任意查询", timeout=1.0)
    assert out["intent"] == settings.DEFAULT_INTENT


def test_hybrid_search_passes_intent_budget(monkeypatch):
    """hybrid_search 的 budget_s 必须分一份给 intent（真调用链，不 mock 逻辑）。

    这是病灶的正面回归：修复前 `classify_intent(query)` 单参调用，
    `captured` 里拿不到 timeout → 红。
    """
    captured = {}

    def fake_classify(query, timeout=None):
        captured["timeout"] = timeout
        captured["query"] = query
        return {"intent": "fact_lookup", "candidate_n": 2}

    monkeypatch.setattr(hybrid, "classify_intent", fake_classify)
    # 外部面只 mock 网络/存储（不 mock 被测逻辑）：embed 返回定长向量、
    # 向量库返回空 → 走关键词兜底，hybrid_search 全流程真实执行。
    monkeypatch.setattr(hybrid, "embed", lambda texts, timeout=None: [[0.0] * 8 for _ in texts])
    monkeypatch.setattr(hybrid, "get_vector_store", lambda: _EmptyVectorStore())

    out = hybrid.hybrid_search("任意查询", top_k=3, budget_s=6.8, trace=True)

    assert captured.get("timeout") is not None, "hybrid_search 没把预算传给 classify_intent"
    assert 0 < captured["timeout"] < 6.8, (
        f"传给意图分类的预算 {captured['timeout']!r} 不在 (0, 6.8) 内"
    )
    assert isinstance(out, tuple)  # trace=True 返回 (results, steps)


def test_hybrid_search_without_budget_keeps_old_signature(monkeypatch):
    """无预算路径保持旧行为：classify_intent 仍以单参调用（零回归）。"""
    calls = []

    def fake_classify(query, timeout=None):
        calls.append(timeout)
        return {"intent": "fact_lookup", "candidate_n": 2}

    monkeypatch.setattr(hybrid, "classify_intent", fake_classify)
    monkeypatch.setattr(hybrid, "embed", lambda texts, timeout=None: [[0.0] * 8 for _ in texts])
    monkeypatch.setattr(hybrid, "get_vector_store", lambda: _EmptyVectorStore())

    hybrid.hybrid_search("任意查询", top_k=3, trace=True)
    assert calls == [None], f"无预算时 timeout 应为 None（旧签名等价），实得 {calls!r}"
