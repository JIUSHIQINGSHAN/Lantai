"""进程级 LLM 并发闸门（票据 .scratch/llm-concurrency-gate/issues/01）。

不 mock 冒烟纪律：真实 threading.BoundedSemaphore + 真实函数体直调。
唯一替身：embed/chat_json 的网络客户端（get_client）换成**本地计数桩**——
被测对象是闸门逻辑，不是网络；这是「外部网络 mock 允许」的既有豁免面
（测试网络逃逸教训：未 patch 的 embed 会绑真 key 连真网）。
"""

import threading
import time

import pytest

from lantai.core.settings import settings
from lantai.llm import client as llm_client

# conftest 的 autouse 替身（_stub_external_llm）会在 fixture 层把
# embed/chat_json/vision_caption 的模块属性换成假货。本文件测的是**闸门
# 逻辑**（真实函数体 + 真实 Semaphore + 本地计数桩网络层），所以 import 时
# 先捕获真实函数，各用例里再顶回去——晚于 autouse fixture 生效，撕卸后
# monkeypatch 自动还原（conftest 注释里明写的既有范式）。
_REAL_chat_json = llm_client.chat_json
_REAL_embed = llm_client.embed
_REAL_vision_caption = llm_client.vision_caption


class _Counter:
    """闸内并发计数器：记录同时在闸内的请求数峰值。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def enter(self):
        with self.lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def exit(self):
        with self.lock:
            self.current -= 1


@pytest.fixture()
def gate_env(monkeypatch):
    """每个用例独立重建闸门单例与计数桩（模块级单例不跨用例串场）。

    顺带把真实函数顶回模块属性（conftest autouse 替身晚于 import 生效，
    这里再晚于它生效）；monkeypatch 撕卸时一并还原为替身，不影响别的文件。
    """
    counter = _Counter()
    monkeypatch.setattr(llm_client, "_llm_gate", None)  # 强制按本用例设置重建
    monkeypatch.setattr(llm_client, "chat_json", _REAL_chat_json)
    monkeypatch.setattr(llm_client, "embed", _REAL_embed)
    monkeypatch.setattr(llm_client, "vision_caption", _REAL_vision_caption)
    return counter


def _stub_chat_client(counter):
    """返回替身 OpenAI 客户端：进入即计数、睡一拍模拟外呼耗时。"""

    class _Msg:
        content = "{}"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            counter.enter()
            time.sleep(0.05)
            counter.exit()
            return _Resp()

    class _Client:
        chat = type("C", (), {"completions": _Completions()})()

    return _Client()


def _stub_embed_client(counter):
    class _Data:
        embedding = [0.0]

    class _Resp:
        data = [_Data()]

    class _Embeddings:
        def create(self, **kwargs):
            counter.enter()
            time.sleep(0.05)
            counter.exit()
            return _Resp()

    class _Client:
        embeddings = _Embeddings()

    return _Client()


def _stub_full_client(counter):
    """双通道桩：chat 与 embeddings 齐备（混合用例用，缺一边会 AttributeError）。"""

    class _Full(_stub_chat_client(counter).__class__):
        pass

    base = _stub_chat_client(counter)
    emb = _stub_embed_client(counter)
    base.embeddings = emb.embeddings
    return base


def _run_threads(fn, n):
    threads = [threading.Thread(target=fn) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads), "worker thread hung"


# ---------------------------------------------------------------- 并发上限


def test_chat_json_bounded_by_gate(gate_env, monkeypatch):
    """8 线程抢 2 许可：闸内峰值 ≤ 2（修前无闸时峰值 = 8）。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "LLM_ACQUIRE_TIMEOUT", 10)
    monkeypatch.setattr(llm_client, "get_client", lambda: _stub_chat_client(gate_env))

    _run_threads(lambda: llm_client.chat_json("sys", "usr"), 8)
    assert gate_env.peak <= 2, f"gate leaked: peak={gate_env.peak}"


def test_embed_shares_same_gate(gate_env, monkeypatch):
    """三通道同闸（上游教训：只闸 chat 不闸 embed 等于没闸）：
    chat 与 embed 同时并发，合计峰值 ≤ 许可数。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "LLM_ACQUIRE_TIMEOUT", 10)
    monkeypatch.setattr(llm_client, "get_client", lambda: _stub_full_client(gate_env))

    def mixed():
        llm_client.chat_json("s", "u")
        llm_client.embed(["x"])

    _run_threads(mixed, 6)
    assert gate_env.peak <= 2, f"channels not sharing one gate: peak={gate_env.peak}"


def test_downgrade_to_one_permit(gate_env, monkeypatch):
    """降配实证：LLM_MAX_CONCURRENCY=1 时峰值恒 1（串行化）。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "LLM_ACQUIRE_TIMEOUT", 10)
    monkeypatch.setattr(llm_client, "get_client", lambda: _stub_embed_client(gate_env))

    _run_threads(lambda: llm_client.embed(["x"]), 8)
    assert gate_env.peak == 1, f"serial gate not serial: peak={gate_env.peak}"


# ---------------------------------------------------------------- 等位超时


def test_acquire_timeout_raises(gate_env, monkeypatch):
    """许可占满 + 等位超时 → LLMConcurrencyTimeout，不排队到死。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "LLM_ACQUIRE_TIMEOUT", 0.2)
    monkeypatch.setattr(llm_client, "get_client", lambda: _stub_chat_client(gate_env))

    gate = llm_client._get_gate()
    assert gate.acquire(timeout=5)  # 手动占满唯一许可
    try:
        with pytest.raises(llm_client.LLMConcurrencyTimeout):
            llm_client.chat_json("s", "u")
    finally:
        gate.release()


# ---------------------------------------------------------------- 异常放行


def test_permit_released_on_exception(gate_env, monkeypatch):
    """闸内抛异常后许可必须归还：下一个请求照常通过（with/finally 语义）。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "LLM_ACQUIRE_TIMEOUT", 5)

    class _Boom:
        def __enter__(self):
            raise RuntimeError("inside gate")

        def __exit__(self, *a):
            return False

    gate = llm_client._get_gate()
    # 直接验证闸门的 with 语义：占住 → 抛异常 → 归还 → 仍可获取
    with pytest.raises(RuntimeError):
        with llm_client._gate_acquire():
            raise RuntimeError("inside gate")
    assert gate.acquire(timeout=1), "permit leaked after exception"
    gate.release()


def test_gate_rebuilds_when_singleton_reset(gate_env, monkeypatch):
    """单例重置后按新设置重建（fixture 置 None 的语义与测试隔离同源）。"""
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 3)
    g1 = llm_client._get_gate()
    assert g1._value == 3
    llm_client._llm_gate = None
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 5)
    g2 = llm_client._get_gate()
    assert g2._value == 5 and g2 is not g1
