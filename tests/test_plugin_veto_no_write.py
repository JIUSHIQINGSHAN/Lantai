"""Hermes 否决的一轮不写进记忆（周报 2026-11-03 P0-1）。

Hermes v0.22.6 起，插件可以在 pre_llm_call 返回 {"action": "block"} 否决一轮（DLP 拦密码、
密钥）。这一轮不发给模型，on_session_end 的 turn_exit_reason 是
"blocked_by_plugin_pre_llm_call"。lantai-hook 在 pre_llm_call 一进来就把原话放进会话缓冲，
修复前 on_session_end 不看 turn_exit_reason，原话照样送进对话写通道和底本五段块。
测试不依赖 Hermes 进程，serve 子进程交互全部 mock。
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"
BLOCKED = "blocked_by_plugin_pre_llm_call"
SECRET = "我的银行卡密码是 864213，帮我记一下"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("lantai_hook_veto", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


def _pre(mod, text, sid="s1", turn_id=None):
    kw = {"user_message": text, "session_id": sid}
    if turn_id is not None:
        kw["turn_id"] = turn_id
    with (
        patch.object(mod, "_call_hook", return_value=None),
        patch.object(mod, "_call_checkpoint", return_value=None),
    ):
        mod._on_pre_llm_call(**kw)


def _end(mod, sid="s1", **kw):
    with (
        patch.object(mod, "_call_dialogue") as dlg,
        patch.object(mod, "_call_checkpoint_write") as cw,
    ):
        mod._on_session_end(session_id=sid, **kw)
    return dlg, cw


def _texts(dlg):
    return [c.args[0] for c in dlg.call_args_list]


def test_exit_reason_matches_hermes_constant(mod):
    # Hermes agent/turn_context.py: PRE_LLM_BLOCK_EXIT_REASON = "blocked_by_plugin_pre_llm_call"
    assert mod._PRE_LLM_BLOCK_EXIT_REASON == BLOCKED


def test_vetoed_turn_not_written_to_dialogue_or_checkpoint(mod):
    _pre(mod, SECRET, turn_id="t1")
    dlg, cw = _end(
        mod,
        turn_id="t1",
        completed=True,
        failed=False,
        interrupted=False,
        turn_exit_reason=BLOCKED,
    )
    dlg.assert_not_called()
    cw.assert_not_called()
    assert "s1" not in mod._session_buffers


def test_normal_turn_still_written(mod):
    _pre(mod, "记住：我对花生过敏", turn_id="t1")
    dlg, cw = _end(mod, turn_id="t1", completed=True, turn_exit_reason="text_response(stop)")
    dlg.assert_called_once_with("记住：我对花生过敏", session_id="s1", turn=1)
    cw.assert_called_once()
    assert cw.call_args[0][1]["cp_active_intent"] == "记住：我对花生过敏"


def test_interrupted_turn_behaviour_unchanged(mod):
    # 只处理否决；被中断的一轮已经发给过模型，照旧写入（是否写是另一个产品问题）
    _pre(mod, "我想把周报改到周日发", turn_id="t1")
    dlg, _ = _end(mod, turn_id="t1", interrupted=True, turn_exit_reason="interrupted_by_user")
    assert _texts(dlg) == ["我想把周报改到周日发"]


def test_earlier_unflushed_turn_kept_vetoed_dropped(mod):
    # 缓冲里还有尚未 flush 的正常轮次（例如宿主漏发了一次 on_session_end）：只剔否决那轮
    _pre(mod, "以后都用中文回复我", turn_id="t1")
    _pre(mod, SECRET, turn_id="t2")
    dlg, cw = _end(mod, turn_id="t2", turn_exit_reason=BLOCKED)
    assert _texts(dlg) == ["以后都用中文回复我"]
    assert dlg.call_args.kwargs == {"session_id": "s1", "turn": 1}
    assert cw.call_args[0][1]["cp_active_intent"] == "以后都用中文回复我"


def test_next_turn_after_veto_written_normally(mod):
    _pre(mod, SECRET, turn_id="t1")
    _end(mod, turn_id="t1", turn_exit_reason=BLOCKED)
    _pre(mod, "好的，那帮我查一下明天的天气", turn_id="t2")
    dlg, _ = _end(mod, turn_id="t2", turn_exit_reason="text_response(stop)")
    assert _texts(dlg) == ["好的，那帮我查一下明天的天气"]
    assert SECRET not in str(dlg.call_args_list)


def test_old_host_without_turn_id_drops_last(mod):
    # 旧版 Hermes 不传 turn_id：否决和 on_session_end 在同一轮内，末条就是被否决的那句
    _pre(mod, "我在做兰台的遗忘模块")
    _pre(mod, SECRET)
    dlg, _ = _end(mod, turn_exit_reason=BLOCKED)
    assert _texts(dlg) == ["我在做兰台的遗忘模块"]


def test_turn_id_mismatch_drops_nothing(mod):
    # 否决的是一轮空消息（没进缓冲），缓冲里只有先前正常的轮次：不能误删
    _pre(mod, "记住：下周三交开题报告", turn_id="t1")
    _pre(mod, "   ", turn_id="t2")
    dlg, _ = _end(mod, turn_id="t2", turn_exit_reason=BLOCKED)
    assert _texts(dlg) == ["记住：下周三交开题报告"]


def test_drop_vetoed_turn_unit(mod):
    # 返回口径（v0.67 票 01）：int → 被剔条目列表（否决分支要拿 event_id 发 veto 帧）
    mod._buffer_turn("s1", "a", turn_id="t1")
    mod._buffer_turn("s1", "b", turn_id="t2")
    assert mod._drop_vetoed_turn("s1", "t9") == []
    assert mod._drop_vetoed_turn("s1", "") == []  # 末条带着别的 turn_id，不删
    dropped = mod._drop_vetoed_turn("s1", "t2")
    assert [e["text"] for e in dropped] == ["b"]
    assert [e["text"] for e in mod._session_buffers["s1"]] == ["a"]
    assert mod._drop_vetoed_turn("", "t1") == []
    assert mod._drop_vetoed_turn("nope", "t1") == []


def test_buffer_entry_shape_unchanged_without_turn_id(mod):
    # 宿主不传 turn_id 时条目形状与修复前一致（其他测试和下游依赖它）
    _pre(mod, "我最近在学知识图谱")
    assert mod._session_buffers["s1"] == [{"text": "我最近在学知识图谱", "turn": 1}]
    _pre(mod, "第二句", turn_id="t7")
    assert mod._session_buffers["s1"][1] == {"text": "第二句", "turn": 2, "turn_id": "t7"}
