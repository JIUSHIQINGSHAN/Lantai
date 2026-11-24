"""宿主帧适配测试（票 06 宿主矩阵 / 片 03）。

缝隙：`adapt_response(host, result) -> dict`（纯函数）与 `HOST_EVENT` 常量。

事实依据（官方文档一手来源，2026-09-25/26）：
- Claude Code 与 Codex CLI 的注入通道均为
  `{"hookSpecificOutput": {"hookEventName": "<事件>", "additionalContext": "..."}}`；
- 二者 `UserPromptSubmit` 事件请求帧均带 `prompt` 字段（已被归一化层的 query
  回退覆盖，无需请求侧适配）；
- Hermes 走插件通道，吃兰台自有 `{context, evidence, event_id}` 形状（直通）。
"""

import json

import pytest

from lantai.integrations.host_adapters import (
    HOST_EVENT,
    INJECTION_EVENTS,
    adapt_response,
)


class TestAdaptResponseHermes:
    """Hermes（直通）：响应形状不变。"""

    def test_passthrough_unchanged(self):
        result = {"context": "记忆正文", "event_id": "rev_1", "request_id": "req_1"}
        assert adapt_response("hermes", result) == result

    def test_default_host_is_passthrough(self):
        """未指定宿主（None）→ 直通，兼容旧行为。"""
        result = {"context": "x"}
        assert adapt_response(None, result) == result

    def test_empty_result_stays_empty(self):
        assert adapt_response("hermes", {}) == {}


class TestAdaptResponseClaudeCode:
    def test_wraps_into_additional_context(self):
        out = adapt_response(
            "claude-code",
            {"context": "记忆正文", "event_id": "rev_1", "request_id": "req_1"},
        )
        assert out["hookSpecificOutput"] == {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "记忆正文",
        }

    def test_carries_metadata_for_receipt(self):
        """event_id/request_id 须带出——回执（backfill）需要它。

        依据（code.claude.com/docs/en/hooks-guide）：未知顶层键被静默忽略
        （仅记 debug 日志「unrecognized keys」），不报错；仅 Stop 事件严格校验，
        而本协议只用 UserPromptSubmit。
        """
        out = adapt_response(
            "claude-code",
            {"context": "正文", "event_id": "rev_9", "request_id": "req_9"},
        )
        assert out["event_id"] == "rev_9"
        assert out["request_id"] == "req_9"

    def test_empty_result_yields_empty(self):
        """无命中 → 空输出（宿主视作无意见），不产出空 additionalContext。"""
        assert adapt_response("claude-code", {}) == {}

    def test_result_without_context_never_injects(self):
        """无 context → 绝不产出 additionalContext（不注入空上下文条目）。"""
        out = adapt_response("claude-code", {"event_id": "rev_1"})
        assert "hookSpecificOutput" not in out

    def test_write_path_result_passes_through(self):
        """回执/对话等控制面应答原样返回——包成 additionalContext 会抹掉
        `receipt_status`，宿主侧无从知晓回执是否成功。"""
        ack = {"ok": True, "event_id": "rev_1", "used_count": 1, "receipt_status": "acked"}
        assert adapt_response("claude-code", ack) == ack
        assert adapt_response("codex", ack) == ack

    def test_output_is_json_serializable(self):
        out = adapt_response("claude-code", {"context": "中文"})
        assert "中文" in json.dumps(out, ensure_ascii=False)


class TestAdaptResponseCodex:
    def test_same_shape_as_claude_code(self):
        """Codex 注入通道形状与 Claude Code 相同（官方文档实证）。"""
        result = {"context": "记忆正文"}
        assert adapt_response("codex", result) == adapt_response("claude-code", result)

    def test_empty_result_yields_empty(self):
        assert adapt_response("codex", {}) == {}


class TestWiredThroughShellHook:
    """适配层经 `shell_hook._handle_one` 真实可达（不只在单测里自证）。"""

    def _load(self, monkeypatch):
        import importlib.util
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "shell_hook.py",
        )
        spec = importlib.util.spec_from_file_location("shell_hook", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
        return mod

    def test_claude_code_env_wraps_context(self, monkeypatch):
        mod = self._load(monkeypatch)
        monkeypatch.setenv("LANTAI_HOST", "claude-code")
        monkeypatch.setattr(mod, "build_context", lambda q, s: {"context": "记忆正文"})
        out = mod._handle_one('{"prompt":"上次说的方案"}')
        assert out["hookSpecificOutput"]["additionalContext"] == "记忆正文"

    def test_no_host_env_passthrough(self, monkeypatch):
        mod = self._load(monkeypatch)
        monkeypatch.delenv("LANTAI_HOST", raising=False)
        monkeypatch.setattr(mod, "build_context", lambda q, s: {"context": "记忆正文"})
        assert mod._handle_one('{"prompt":"上次说的方案"}') == {"context": "记忆正文"}

    def test_hermes_env_passthrough(self, monkeypatch):
        mod = self._load(monkeypatch)
        monkeypatch.setenv("LANTAI_HOST", "hermes")
        monkeypatch.setattr(mod, "build_context", lambda q, s: {"context": "记忆正文"})
        assert mod._handle_one('{"query":"上次说的方案"}') == {"context": "记忆正文"}


class TestHostRegistry:
    def test_all_matrix_hosts_have_event(self):
        """三命令钩子宿主均已登记兜底事件名（仅在拿不到真实事件时使用）。"""
        for host in ("claude-code", "codex"):
            assert host in HOST_EVENT
        assert HOST_EVENT["claude-code"] == "UserPromptSubmit"
        assert HOST_EVENT["codex"] == "UserPromptSubmit"

    def test_unknown_host_passthrough(self):
        """未登记宿主 → 直通（不破坏未来宿主的自有形状）。"""
        result = {"context": "x"}
        assert adapt_response("future-host", result) == result


class TestEventAwareInjection:
    """票 .scratch/multi-client-adaptation-20261124/issues/01。

    Claude Code 2.1.330 真机对照（`claude --settings <f> -p` + 标记串问模型）：
    钩子返回 `hookEventName: UserPromptSubmit` 而实际触发事件是 `SessionStart`
    → 模型答 NO（整帧被丢弃）；改成 `SessionStart` → 答 YES。

    故事件名必须**跟随触发事件**，宿主固定值只在拿不到事件时兜底。
    """

    def test_session_start_keeps_its_own_event_name(self):
        out = adapt_response("claude-code", {"context": "底本"}, event="SessionStart")
        assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_user_prompt_submit_unchanged(self):
        out = adapt_response("claude-code", {"context": "x"}, event="UserPromptSubmit")
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_codex_follows_event_too(self):
        out = adapt_response("codex", {"context": "x"}, event="SessionStart")
        assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    @pytest.mark.parametrize("event", ["Stop", "SessionEnd", "PreCompact", "Notification"])
    def test_non_injection_events_never_inject(self, event):
        """非注入事件原样返回：Stop 等拿到画像帧只会往上下文灌噪音。"""
        result = {"context": "画像兜底"}
        assert adapt_response("claude-code", result, event=event) == result

    def test_non_injection_event_keeps_write_path_untouched(self):
        ack = {"ok": True, "event_id": "rev_1", "receipt_status": "acked"}
        assert adapt_response("claude-code", ack, event="Stop") == ack

    def test_missing_event_falls_back_to_host_default(self):
        """事件缺失（旧调用方 / 手工帧）→ 宿主默认值，旧行为逐字不变。"""
        out = adapt_response("claude-code", {"context": "x"})
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_empty_event_string_falls_back(self):
        out = adapt_response("claude-code", {"context": "x"}, event="")
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_injection_event_whitelist_matches_documented_set(self):
        assert frozenset({"SessionStart", "UserPromptSubmit", "SubagentStart"}) == INJECTION_EVENTS
