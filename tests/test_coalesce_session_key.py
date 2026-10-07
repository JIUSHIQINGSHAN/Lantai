"""潮波按会话分键（票据 .scratch/coalesce-session-key/issues/01）。

不 mock 冒烟：真实 CoalesceBuffer，不起线程、不走 LLM。
上游 f0.3 教训镜像：多会话同 user 消息混并 = 记忆串场 + 出身丢失。
"""

import time

import pytest

from lantai.core.settings import settings
from lantai.ingestion.coalesce import CoalesceBuffer


@pytest.fixture()
def buf():
    """独立缓冲实例（全局单例不跨用例串场）+ 快窗口让判定可测。"""
    b = CoalesceBuffer()
    # general profile: window/idle 缺省大——把窗口改小以便窗口冲刷可触发
    orig = dict(settings.LANE_COALESCE_PROFILES)
    settings.LANE_COALESCE_PROFILES["general"] = {
        **orig["general"],
        "window": 0.05,
        "idle_timeout": 0.05,
        "max_parts": 100,
        "max_chars": 100000,
    }
    yield b
    settings.LANE_COALESCE_PROFILES.clear()
    settings.LANE_COALESCE_PROFILES.update(orig)


# ---------------------------------------------------------------- 分键隔离


def test_same_user_different_sessions_do_not_mix(buf):
    """同 user 的 A/B 两会话消息不得混进同一缓冲：A 窗口冲刷只带 A 的消息。"""
    buf.add("u1", "general", "A会话的第一句话", session_id="sess-A")
    buf.add("u1", "general", "B会话的私密内容", session_id="sess-B")

    time.sleep(0.06)
    flushed = buf.check_idle()

    assert flushed, "两个会话都应窗口超时冲刷"
    keys = [r["key"] for r in flushed]
    assert len(keys) == 2, f"应分键冲刷两批，实际 keys={keys}"
    by_key = {r["key"]: r for r in flushed}
    a = next(v for k, v in by_key.items() if "sess-A" in k)
    assert "A会话的第一句话" in a["combined_content"]
    assert "B会话的私密内容" not in a["combined_content"], "A 冲刷混入了 B 的消息"


def test_flush_result_carries_session(buf):
    """冲刷结果带回分键时的真 session（worker 持久化的出身凭据）。"""
    buf.add("u1", "general", "带出身的消息", session_id="sess-A")
    time.sleep(0.06)
    flushed = buf.check_idle()
    assert flushed
    assert flushed[0]["session_id"] == "sess-A"


def test_job_id_includes_session(buf):
    """幂等指纹含 session：同内容同 user 不同 session 不误判 duplicate。"""
    r1 = buf.add_async("u1", "general", "同内容", session_id="sess-A")
    r2 = buf.add_async("u1", "general", "同内容", session_id="sess-B")
    assert r1["job_id"] != r2["job_id"], "不同 session 的同内容应各自入队"
    assert not r2.get("duplicate")


# ---------------------------------------------------------------- 兼容与解析


def test_empty_session_backward_compat(buf):
    """session 空串 = 旧键行为：buffered/flushed/water_level 语义不漂移。"""
    r = buf.add("u1", "general", "无出身消息", session_id="")
    assert r.get("buffered") or r.get("flushed")
    time.sleep(0.06)
    flushed = buf.check_idle()
    assert flushed and flushed[0]["session_id"] == ""
    # 冲刷后水位归零
    assert buf.water_level()["active_keys"] == 0


def test_worker_key_parse_with_colon_in_user(buf):
    """user_id 含冒号时，worker 的键解析（rsplit 尾部两段）仍正确。"""
    buf.add("weird:user", "general", "奇怪属主的消息", session_id="sess-X")
    time.sleep(0.06)
    flushed = buf.check_idle()
    assert flushed
    key = flushed[0]["key"]
    # 模拟 ingest_worker 的解析：尾部两段是 lane 与 session
    _, lane, session = key.rsplit(":", 2)
    assert lane == "general"
    assert session == "sess-X"


def test_default_session_param_unchanged_callers(buf):
    """不传 session_id 的既有调用方：行为与修前逐字一致（默认空串入键）。"""
    r1 = buf.add("u1", "general", "老调用一")
    r2 = buf.add("u1", "general", "老调用二")
    assert r1.get("buffered") and r2.get("buffered")
    time.sleep(0.06)
    flushed = buf.check_idle()
    assert len(flushed) == 1
    assert flushed[0]["count"] == 2
    assert flushed[0]["session_id"] == ""


# ---------------------------------------------------------------- worker 透传


def test_worker_persists_session_lineage(buf, monkeypatch):
    """worker 空闲冲刷持久化时出身随键透传（M2 变异杀手段）。

    ingest_worker.run_coalesce_idle 构造 AddMemoryReq 必须带 flush 解析出的
    session_id——漏掉则潮波路径的来源链在 worker 侧断头。
    """
    from unittest.mock import patch

    from lantai.services import memory_service as ms
    from lantai.workers import ingest_worker

    buf.add("u1", "general", "worker 冲刷的第一条足够长内容", session_id="sess-W")
    time.sleep(0.06)

    captured = {}

    def fake_persist(req, **kwargs):
        captured["session_id"] = req.session_id
        return {"document_id": "doc_x"}

    with (
        patch("lantai.ingestion.coalesce.get_coalesce_buffer", return_value=buf),
        # worker 内是函数级 `from ... import memory_service as ms`——
        # patch 源模块属性（import 时绑定的是模块对象，属性查找运行时解析）
        patch.object(ms, "_create_candidate_with_extraction", side_effect=fake_persist),
    ):
        ingest_worker.run_coalesce_idle()

    assert captured.get("session_id") == "sess-W", (
        f"worker 丢弃了 session 出身: captured={captured}"
    )
    assert buf.water_level()["total_messages"] == 0
