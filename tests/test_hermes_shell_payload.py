"""Hermes shell hook 原生线格式 → CLI 模式检索（shell_hook.py）。

Hermes v0.23.1 给 shell hook 的 stdin 是 `{hook_event_name, tool_name, tool_input,
session_id, cwd, profile, extra}`，本轮用户原话在 `extra.user_message`，顶层没有
`query`/`message`/`prompt`。修复前 `parse_host_request` 只认顶层三字段，CLI 模式
每轮都当空查询——真 Hermes 端到端 4 轮返回 4 个 `{}`，retrieval_event 0 行。

`REAL_PAYLOAD` 取自真 Hermes v0.23.1 `hermes chat` 一轮实际喂给 hook 的 stdin
（只缩短了 id 和 conversation_history，换掉了本机路径）。
"""

import importlib.util
import json
import os
from unittest.mock import patch

from lantai.integrations.host_protocol import ACTION_QUERY, parse_host_request

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)

USER_MSG = "兰台发版流程是什么？我上次做到哪了"

REAL_PAYLOAD = {
    "hook_event_name": "pre_llm_call",
    "tool_name": None,
    "tool_input": None,
    "session_id": "local-67e843eb",
    "cwd": "/srv/project",
    "profile": "default",
    "extra": {
        "task_id": "local-67e843eb",
        "turn_id": "local-67e843eb:local-67e843eb:993676d2",
        "user_message": USER_MSG,
        "conversation_history": [{"role": "user", "content": USER_MSG}],
        "is_first_turn": True,
        "model": "fake-model",
        "platform": "cli",
        "sender_id": "uid:1001",
        "telemetry_schema_version": "hermes.observer.v1",
        "kanban_task_id": "",
    },
}


def _payload(**overrides) -> dict:
    d = json.loads(json.dumps(REAL_PAYLOAD))
    extra = overrides.pop("extra", None)
    if extra is not None:
        d["extra"].update(extra)
    d.update(overrides)
    return d


# ── 归一化层 ──


def test_real_hermes_payload_yields_user_message_as_query():
    req = parse_host_request(json.dumps(REAL_PAYLOAD, ensure_ascii=False))
    assert req is not None
    assert req.action == ACTION_QUERY
    assert req.query == USER_MSG
    assert req.session_id == "local-67e843eb"


def test_top_level_query_still_wins_over_extra():
    """既有调用方（插件 / 自写脚本）传顶层 query 的语义不变。"""
    for key in ("query", "message", "prompt"):
        req = parse_host_request(json.dumps(_payload(**{key: "顶层优先的查询"})))
        assert req.query == "顶层优先的查询", key


def test_other_hook_events_keep_empty_query():
    """只认 pre_llm_call；on_session_start 等其他事件即使 extra 里有 user_message 也不检索。"""
    for event in ("on_session_start", "post_llm_call", "pre_tool_call", None):
        req = parse_host_request(json.dumps(_payload(hook_event_name=event)))
        assert req.query == "", event


def test_multimodal_user_message_takes_text_parts():
    parts = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "这张图里的报错"},
        {"type": "text", "text": "上次怎么修的"},
    ]
    req = parse_host_request(json.dumps(_payload(extra={"user_message": parts})))
    assert req.query == "这张图里的报错\n上次怎么修的"


def test_malformed_extra_degrades_to_empty_query():
    bad = [
        _payload(extra=None) | {"extra": None},
        _payload() | {"extra": "str"},
        _payload() | {"extra": [USER_MSG]},
        _payload(extra={"user_message": 123}),
        _payload(extra={"user_message": None}),
        _payload(
            extra={"user_message": [{"type": "image_url"}, "raw", {"type": "text", "text": 5}]}
        ),
    ]
    for d in bad:
        req = parse_host_request(json.dumps(d))
        assert req is not None
        assert req.query == "", d["extra"]


# ── shell_hook.py 全链路（_handle_one → build_context）──


def _load_hook(monkeypatch):
    spec = importlib.util.spec_from_file_location("shell_hook_hermes_payload", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 30.0)
    return mod


def test_shell_hook_retrieves_with_real_hermes_payload(monkeypatch):
    """真 Hermes 的一帧进来，build_context 拿到的是本轮原话和 Hermes 的 session_id。"""
    mod = _load_hook(monkeypatch)
    seen = {}

    def fake_build(query, session_id=None):
        seen["query"], seen["session_id"] = query, session_id
        return {"context": "[相关记忆] 兰台发版流程"}

    monkeypatch.setattr(mod, "build_context", fake_build)
    out = mod._handle_one(json.dumps(REAL_PAYLOAD, ensure_ascii=False))
    assert seen == {"query": USER_MSG, "session_id": "local-67e843eb"}
    assert out.get("context") == "[相关记忆] 兰台发版流程"


def test_shell_hook_logs_retrieval_with_hermes_session(monkeypatch):
    """不 mock build_context：检索埋点真的被调用，带上原话和 Hermes session_id。"""
    mod = _load_hook(monkeypatch)
    monkeypatch.setattr(mod, "relevance_check", lambda q, **kw: {"needs_memory": True})
    with patch("lantai.observability.retrieval_log.log_retrieval", return_value="rev_x") as m:
        mod._handle_one(json.dumps(REAL_PAYLOAD, ensure_ascii=False))
    assert m.called, "真 Hermes 线格式下检索一次都没跑"
    assert m.call_args.kwargs.get("session_id") == "local-67e843eb"
    args_text = json.dumps(
        [str(a) for a in m.call_args.args] + [str(v) for v in m.call_args.kwargs.values()],
        ensure_ascii=False,
    )
    assert USER_MSG in args_text
