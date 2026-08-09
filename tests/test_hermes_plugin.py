"""Hermes remembrance-hook 插件测试（v0.5 对话自动写入）。

插件源码在仓库 hermes-plugin/remembrance-hook/（部署脚本同步到 Hermes home）。
测试不依赖 Hermes 进程——register(ctx) 用假 ctx；serve 子进程交互全部 mock。
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "remembrance-hook" / "__init__.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("remembrance_plugin", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._proc = None
    m._proc_ready = False
    return m


class TestSessionBuffer:
    """会话缓冲：pre_llm_call 累积 user_message，on_session_end flush"""

    def test_buffer_accumulates_and_flushes(self, mod):
        mod._buffer_turn("sess_1", "记住：明天下午3点开会")
        mod._buffer_turn("sess_1", "我最近喜欢用 Rust 写 CLI")
        assert mod._session_buffers["sess_1"] == [
            "记住：明天下午3点开会", "我最近喜欢用 Rust 写 CLI"]
        with patch.object(mod, "_call_dialogue") as m:
            mod._flush_session("sess_1")
        assert m.call_count == 2
        assert "sess_1" not in mod._session_buffers

    def test_buffer_ignores_empty(self, mod):
        mod._buffer_turn("sess_1", "   ")
        mod._buffer_turn("", "内容")
        assert mod._session_buffers == {}

    def test_buffer_cap_trims_oldest(self, mod):
        mod._SESSION_BUFFER_MAX_MSGS = 3
        for i in range(5):
            mod._buffer_turn("sess_1", f"消息{i}")
        assert len(mod._session_buffers["sess_1"]) == 3
        assert mod._session_buffers["sess_1"][0] == "消息2"

    def test_flush_missing_session_noop(self, mod):
        with patch.object(mod, "_call_dialogue") as m:
            mod._flush_session("sess_nope")
        m.assert_not_called()


class TestCallbacks:
    """pre_llm_call 缓冲 + on_session_end flush"""

    def test_pre_llm_call_buffers_user_message(self, mod):
        with patch.object(mod, "_call_hook", return_value=None):
            mod._on_pre_llm_call(user_message="我最近在学知识图谱",
                                 session_id="sess_1")
        assert mod._session_buffers["sess_1"] == ["我最近在学知识图谱"]

    def test_on_session_end_flushes(self, mod):
        mod._buffer_turn("sess_1", "记住：明天开会")
        with patch.object(mod, "_call_dialogue") as m:
            mod._on_session_end(session_id="sess_1", completed=True)
        m.assert_called_once_with("记住：明天开会")
        assert "sess_1" not in mod._session_buffers

    def test_register_hooks(self, mod):
        hooks = {}

        class _FakeCtx:
            def register_hook(self, name, cb):
                hooks[name] = cb

        mod.register(_FakeCtx())
        assert "pre_llm_call" in hooks
        assert "on_session_end" in hooks
        assert callable(hooks["on_session_end"])

    def test_plugin_yaml_declares_hooks(self):
        yaml_path = PLUGIN_PATH.parent / "plugin.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        assert "pre_llm_call" in text
        assert "on_session_end" in text
