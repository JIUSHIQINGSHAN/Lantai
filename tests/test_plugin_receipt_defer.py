"""注入回执延后到 on_session_end（v0.67 票 01 / ADR-0084）插件侧测试。

病灶：pre_llm_call 检索成功后**立即**回执（_call_backfill），此刻 Hermes 还没
裁决是否否决这一轮——被否决的一轮从未发给模型，回执却已把 used_ids 回填并刷过
injection_count（假注入计数），检索日志里 DLP 拦下的原话也留在 query_text。

改法：回执挂到会话缓冲条目上延后发——on_session_end 知道这一轮未被否决才发；
被否决的条目改为发 retrieval_veto 帧作废检索事件。无缓冲条目（无 session_id）
的轮次保持旧行为立即回执：那些轮次的否决链本来就不可达。

测试不依赖 Hermes 进程，serve 子进程交互全部 mock（同 test_plugin_veto_no_write
惯例：mock 的是进程边界，不是被测函数内部逻辑）。
"""

import contextlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"
BLOCKED = "blocked_by_plugin_pre_llm_call"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("lantai_hook_defer", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


def _hook_response(event_id="rev-1"):
    return {
        "context": "【相关记忆】…",
        "event_id": event_id,
        "evidence": [{"id": "m1", "content": "事实甲", "score": 0.9}],
    }


def _pre(mod, text, sid="s1", turn_id=None, hook=None):
    kw = {"user_message": text, "session_id": sid}
    if turn_id is not None:
        kw["turn_id"] = turn_id
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(mod, "_call_hook", return_value=hook))
        stack.enter_context(patch.object(mod, "_call_checkpoint", return_value=None))
        bf = stack.enter_context(patch.object(mod, "_call_backfill"))
        vt = stack.enter_context(patch.object(mod, "_call_veto"))
        mod._on_pre_llm_call(**kw)
    return bf, vt


def _end(mod, sid="s1", **kw):
    with (
        patch.object(mod, "_call_dialogue") as dlg,
        patch.object(mod, "_call_checkpoint_write") as cw,
        patch.object(mod, "_call_backfill") as bf,
        patch.object(mod, "_call_veto") as vt,
    ):
        mod._on_session_end(session_id=sid, **kw)
    return dlg, cw, bf, vt


class TestReceiptDefer:
    def test_pre_llm_call_no_immediate_receipt(self, mod):
        """检索成功不再立即回执：_call_backfill 不被调，条目挂上 event_id/used_ids。"""
        bf, vt = _pre(mod, "部署怎么做", turn_id="t1", hook=_hook_response())
        bf.assert_not_called()
        vt.assert_not_called()
        entry = mod._session_buffers["s1"][-1]
        assert entry["event_id"] == "rev-1"
        assert entry["used_ids"] == ["m1"]

    def test_normal_session_end_sends_receipt(self, mod):
        """正常 on_session_end：flush 时按条目回执（这一轮确认未被否决）。"""
        _pre(mod, "部署怎么做", turn_id="t1", hook=_hook_response())
        _dlg, _cw, bf, vt = _end(mod, turn_id="t1", turn_exit_reason="text_response(stop)")
        bf.assert_called_once_with("rev-1", ["m1"])
        vt.assert_not_called()

    def test_vetoed_session_end_sends_veto_not_receipt(self, mod):
        """否决 on_session_end：被剔条目发 veto 帧，绝不发回执。"""
        _pre(mod, "我的银行卡密码是 864213", turn_id="t1", hook=_hook_response())
        _dlg, _cw, bf, vt = _end(mod, turn_id="t1", turn_exit_reason=BLOCKED)
        vt.assert_called_once_with("rev-1")
        bf.assert_not_called()

    def test_vetoed_entry_without_event_id_no_veto_call(self, mod):
        """被剔条目没有 event_id（检索没发生/没命中）：veto 不误发。"""
        _pre(mod, "纯文本，检索闸门没放行", turn_id="t1", hook=None)
        _dlg, _cw, bf, vt = _end(mod, turn_id="t1", turn_exit_reason=BLOCKED)
        vt.assert_not_called()
        bf.assert_not_called()

    def test_old_host_veto_drops_last_and_sends_veto(self, mod):
        """旧宿主（无 turn_id）剔末条：末条挂着 event_id 时同样发 veto。

        旧宿主场景的条目一律不挂 turn_id（剔末条语义的前提），见既有
        test_old_host_without_turn_id_drops_last。
        """
        _pre(mod, "以后都用中文回复我")
        _pre(mod, "我的银行卡密码是 864213", hook=_hook_response("rev-2"))
        _dlg, _cw, bf, vt = _end(mod, turn_exit_reason=BLOCKED)
        vt.assert_called_once_with("rev-2")
        bf.assert_not_called()

    def test_mixed_session_receipt_only_for_kept_entries(self, mod):
        """否决只影响那一轮：先前正常轮次的回执照发，被否决轮发 veto。"""
        _pre(mod, "以后都用中文回复我", turn_id="t1", hook=_hook_response("rev-ok"))
        _pre(mod, "我的银行卡密码是 864213", turn_id="t2", hook=_hook_response("rev-bad"))
        _dlg, _cw, bf, vt = _end(mod, turn_id="t2", turn_exit_reason=BLOCKED)
        bf.assert_called_once_with("rev-ok", ["m1"])
        vt.assert_called_once_with("rev-bad")

    def test_no_session_immediate_receipt_unchanged(self, mod):
        """无缓冲条目（无 session_id）：保持旧行为立即回执——否决链对那些轮次不可达。"""
        bf, _vt = _pre(mod, "部署怎么做", sid="", hook=_hook_response())
        bf.assert_called_once_with("rev-1", ["m1"])
        assert "" not in mod._session_buffers

    def test_receipt_not_sent_twice(self, mod):
        """回执只发一次：flush 消费过的条目不重发（服务端重放幂等是底线，不是依赖）。"""
        _pre(mod, "部署怎么做", turn_id="t1", hook=_hook_response())
        _dlg, _cw, bf, _vt = _end(mod, turn_id="t1", turn_exit_reason="text_response(stop)")
        assert bf.call_count == 1
        # 二次 end（宿主重复发 on_session_end）：缓冲已清空，无回执可发
        _dlg2, _cw2, bf2, _vt2 = _end(mod, turn_id="t1", turn_exit_reason="text_response(stop)")
        bf2.assert_not_called()


class TestZeroHitVetoReachable:
    """v0.68 票 01：event_id 挂载与 used_ids 解耦——空 evidence 的被否决轮也发 veto。

    零命中/空 evidence 轮的检索事件已落库（query_text 带 DLP 原话）；挂载条件
    若与 used_ids 耦合，event_id 上不了缓冲条目，_on_session_end 否决分支构造
    不出 retrieval_veto 帧，mark_vetoed 永远够不着。回执条件不变：回执语义是
    「用了哪些」，零 used_ids 无可回执。
    """

    def test_vetoed_zero_hit_entry_sends_veto_without_receipt(self, mod):
        """命中但空 evidence：条目照挂 event_id；否决发 veto、回执不发。"""
        hook = {"context": "【相关记忆】…", "event_id": "rev-zero", "evidence": []}
        _pre(mod, "我的银行卡密码是 864213", turn_id="t1", hook=hook)
        entry = mod._session_buffers["s1"][-1]
        assert entry["event_id"] == "rev-zero"  # 红点：挂载只看 event_id
        assert "used_ids" not in entry  # 空 evidence 无 used_ids 可挂

        _dlg, _cw, bf, vt = _end(mod, turn_id="t1", turn_exit_reason=BLOCKED)
        vt.assert_called_once_with("rev-zero")
        bf.assert_not_called()  # 回执条件不变：used_ids 空则不回执

    def test_zero_hit_normal_end_no_receipt_no_veto(self, mod):
        """对照臂：空 evidence 轮正常结束（未否决）——不回执（无可回执）也不 veto。"""
        hook = {"context": "…", "event_id": "rev-zero", "evidence": []}
        _pre(mod, "部署怎么做", turn_id="t1", hook=hook)
        _dlg, _cw, bf, vt = _end(mod, turn_id="t1", turn_exit_reason="text_response(stop)")
        bf.assert_not_called()
        vt.assert_not_called()


class TestHookResponseWithoutEventId:
    def test_search_hit_but_no_event_id_no_receipt(self, mod):
        """检索命中但响应没带 event_id：无回执可发也不挂载（宁 miss 不脏写）。"""
        hook = {
            "context": "【相关记忆】…",
            "evidence": [{"id": "m1", "content": "x", "score": 0.9}],
        }
        bf, _vt = _pre(mod, "部署怎么做", turn_id="t1", hook=hook)
        bf.assert_not_called()
        entry = mod._session_buffers["s1"][-1]
        assert "event_id" not in entry
        _dlg, _cw, bf2, vt2 = _end(mod, turn_id="t1", turn_exit_reason="text_response(stop)")
        bf2.assert_not_called()
        vt2.assert_not_called()
