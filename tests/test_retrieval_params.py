"""F3 验证与回归测试：RetrievalParams 不可变参数快照与并发安全。

验证：
1. RetrievalParams 支持大写 settings 名和字段名覆盖。
2. hybrid_search 使用 param_overrides 时不修改全局 settings 单例。
3. 并发查询下不同的 param_overrides 互相隔离，杜绝全局竞态污染。
"""
import threading
import pytest
from unittest.mock import patch

from lantai.core.settings import settings
from lantai.retrieval.hybrid import RetrievalParams, hybrid_search


def test_retrieval_params_from_overrides():
    """验证 RetrievalParams 支持大写和简写属性，并且是不可变 frozen 对象。"""
    p = RetrievalParams.from_overrides({
        "RETRIEVAL_W_VECTOR": 0.8,
        "w_bm25": 0.1,
        "custom_key": "val",
    })
    assert p.w_vector == 0.8
    assert p.w_bm25 == 0.1
    assert p.extra.get("custom_key") == "val"
    assert p.get("RETRIEVAL_W_VECTOR") == 0.8
    assert p.get("custom_key") == "val"

    with pytest.raises(Exception):
        p.w_vector = 0.5  # frozen 不可变


def test_param_overrides_does_not_mutate_global_settings(monkeypatch):
    """验证 hybrid_search 传入 param_overrides 时不会篡改全局 settings。"""
    orig_w_vector = settings.RETRIEVAL_W_VECTOR
    orig_w_bm25 = settings.RETRIEVAL_W_BM25

    # mock 内部实现，避免实际调用向量库或数据库
    mock_called = {}

    def fake_impl(*args, **kwargs):
        p = kwargs.get("params")
        mock_called["w_vector"] = p.w_vector
        mock_called["settings_w_vector"] = settings.RETRIEVAL_W_VECTOR
        return []

    monkeypatch.setattr("lantai.retrieval.hybrid._hybrid_search_impl", fake_impl)

    res = hybrid_search("测试查询", param_overrides={"RETRIEVAL_W_VECTOR": 0.999})
    assert res == []
    # 传递给 implementation 的参数生效
    assert mock_called["w_vector"] == 0.999
    # 但全局 settings 绝未被修改
    assert mock_called["settings_w_vector"] == orig_w_vector
    assert settings.RETRIEVAL_W_VECTOR == orig_w_vector
    assert settings.RETRIEVAL_W_BM25 == orig_w_bm25


def test_concurrent_param_overrides_isolation(monkeypatch):
    """并发隔离验证：多个并发调用不同的权重参数，各线程读取互不干扰。"""
    results = {}
    barrier = threading.Barrier(3)

    def fake_impl(query, *args, **kwargs):
        p = kwargs.get("params")
        # 同步等待，确保多个线程同时处于执行中
        barrier.wait(timeout=5)
        # 记录该线程观察到的参数
        results[query] = p.w_vector
        return []

    monkeypatch.setattr("lantai.retrieval.hybrid._hybrid_search_impl", fake_impl)

    def worker(q, w):
        hybrid_search(q, param_overrides={"RETRIEVAL_W_VECTOR": w})

    t1 = threading.Thread(target=worker, args=("q1", 0.1))
    t2 = threading.Thread(target=worker, args=("q2", 0.5))
    t3 = threading.Thread(target=worker, args=("q3", 0.9))

    for t in [t1, t2, t3]:
        t.start()
    for t in [t1, t2, t3]:
        t.join(timeout=5)

    assert results["q1"] == 0.1
    assert results["q2"] == 0.5
    assert results["q3"] == 0.9
