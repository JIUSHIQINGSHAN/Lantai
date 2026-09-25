"""宿主协议归一化层测试（票 06 宿主矩阵 / 片 01）。

缝隙（与维护者确认）：
- `parse_host_request(raw) -> HostRequest | None`（纯函数，无 IO）
- `render_host_response(result) -> str`

既是新层的规格，也是 `_handle_one` 抽出解析逻辑后的等价性锚点：
`tests/test_shell_hook.py` 走 `_handle_one` 的既有断言一行不改仍须通过。
"""

import json

import pytest

from lantai.integrations.host_protocol import (
    HostRequest,
    parse_host_request,
    render_host_response,
)


class TestParseHostRequest:
    """解析与字段校验：判定顺序与结果须与现状 `_handle_one` 逐条一致。"""

    @pytest.mark.parametrize("raw", ["", "   ", "not-json{{{", "[1,2]", "null"])
    def test_invalid_frames_return_none(self, raw):
        """空串/纯空白/非 JSON/非对象 JSON → None（不抛）。"""
        assert parse_host_request(raw) is None

    def test_default_query_with_query_field(self):
        """无 type 但有 query → query 动作。"""
        req = parse_host_request(json.dumps({"query": "部署怎么做"}))
        assert req is not None
        assert req.action == "query"
        assert req.query == "部署怎么做"

    def test_default_query_field_priority(self):
        """无 type：query > message > prompt（现状回退顺序）。"""
        req = parse_host_request(json.dumps({"message": "m", "prompt": "p"}))
        assert req is not None and req.query == "m"
        req2 = parse_host_request(json.dumps({"prompt": "p"}))
        assert req2 is not None and req2.query == "p"

    def test_query_action_via_prompt_field(self):
        """Claude Code 帧只有 prompt——无 type 时须被识别为 query。"""
        req = parse_host_request(json.dumps({"prompt": "上次说的方案"}))
        assert req is not None
        assert req.action == "query"
        assert req.query == "上次说的方案"

    def test_session_id_normalized(self):
        """会话标识经既有归一化：非字符串/空/超长 → None（query 语境默认 None）。"""
        req = parse_host_request(json.dumps({"query": "x", "session_id": "  sess-1  "}))
        assert req is not None and req.session_id == "sess-1"
        req2 = parse_host_request(json.dumps({"query": "x", "session_id": 123}))
        assert req2 is not None and req2.session_id is None

    def test_dialogue_action(self):
        """type=dialogue → 对话动作，text 透传。"""
        req = parse_host_request(json.dumps({"type": "dialogue", "text": "记住：明天开会"}))
        assert req is not None
        assert req.action == "dialogue"
        assert req.text == "记住：明天开会"

    def test_dialogue_turn_contract(self):
        """轮次 1-based：合法 int 透传；0/负/非整数/布尔 → None（宁 miss）。"""
        for bad in (0, -1, "3", True, 2.0, None):
            req = parse_host_request(json.dumps({"type": "dialogue", "text": "x", "turn": bad}))
            assert req is not None and req.turn is None, f"turn={bad!r} 应被拒"
        req = parse_host_request(json.dumps({"type": "dialogue", "text": "x", "turn": 3}))
        assert req is not None and req.turn == 3

    def test_dialogue_session_defaults_empty_string(self):
        """对话语境会话标识缺省为 ""（非 None）——现状差异，须保留。"""
        req = parse_host_request(json.dumps({"type": "dialogue", "text": "x"}))
        assert req is not None and req.session_id == ""

    def test_backfill_action(self):
        req = parse_host_request(
            json.dumps({"type": "backfill", "event_id": "rev_1", "used_ids": ["m1", "m2"]})
        )
        assert req is not None
        assert req.action == "backfill"
        assert req.event_id == "rev_1"
        assert req.used_ids == ("m1", "m2")

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "backfill"},
            {"type": "backfill", "event_id": "rev_1"},
            {"type": "backfill", "event_id": "rev_1", "used_ids": []},
            {"type": "backfill", "event_id": "rev_1", "used_ids": "m1"},
            {"type": "backfill", "event_id": "rev_1", "used_ids": ["m1", 2]},
            {"type": "backfill", "event_id": 123, "used_ids": ["m1"]},
        ],
    )
    def test_backfill_invalid_returns_none(self, payload):
        """无效回执 → None（对应现状 _handle_backfill 的整体拒绝语义）。"""
        assert parse_host_request(json.dumps(payload)) is None

    def test_backfill_request_id_optional(self):
        ok = parse_host_request(
            json.dumps(
                {"type": "backfill", "event_id": "e", "used_ids": ["m"], "request_id": "r1"}
            )
        )
        assert ok is not None and ok.request_id == "r1"
        blank = parse_host_request(
            json.dumps({"type": "backfill", "event_id": "e", "used_ids": ["m"], "request_id": "  "})
        )
        assert blank is not None and blank.request_id is None

    def test_checkpoint_actions(self):
        cp = parse_host_request(json.dumps({"type": "checkpoint"}))
        assert cp is not None and cp.action == "checkpoint"
        cpw = parse_host_request(
            json.dumps({"type": "checkpoint_write", "session_id": "s1", "blocks": {"a": "b"}})
        )
        assert cpw is not None
        assert cpw.action == "checkpoint_write"
        assert cpw.session_id == "s1"
        assert cpw.blocks == {"a": "b"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "checkpoint_write", "session_id": "s1", "blocks": "not-a-dict"},
            {"type": "checkpoint_write", "session_id": 123, "blocks": {}},
        ],
    )
    def test_checkpoint_write_invalid_returns_none(self, payload):
        assert parse_host_request(json.dumps(payload)) is None

    def test_dialogue_empty_text_returns_none(self):
        """对话文本为空/纯空白 → None（现状 _handle_one 校验）。"""
        assert parse_host_request(json.dumps({"type": "dialogue", "text": "   "})) is None
        assert parse_host_request(json.dumps({"type": "dialogue", "text": 123})) is None


class TestRenderHostResponse:
    def test_render_is_utf8_json_no_escapes(self):
        """中文不转义、与现状 ensure_ascii=False 一致。"""
        out = render_host_response({"context": "兰台记忆"})
        assert "兰台记忆" in out
        assert json.loads(out) == {"context": "兰台记忆"}

    def test_render_empty(self):
        assert render_host_response({}) == "{}"


class TestHostRequestShape:
    def test_request_is_immutable(self):
        req = HostRequest(action="query", query="x")
        with pytest.raises(Exception):
            req.action = "dialogue"  # type: ignore[misc]
