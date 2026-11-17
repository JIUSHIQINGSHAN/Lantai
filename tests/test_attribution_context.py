"""Pure real-input smoke tests for attribution parsing; no ambient context."""

import pytest
from pydantic import ValidationError


def test_resolve_origin_and_request_are_distinct():
    from lantai.core.attribution import resolve_attribution

    got = resolve_attribution(
        "history-A", {"version": 1, "request_session_id": "live-B", "request_id": "call-1"}
    )
    assert got == {
        "session_id": "history-A",
        "request_session_id": "live-B",
        "request_id": "call-1",
        "attribution_state": "conflict",
    }


def test_fallback_missing_and_no_global_state():
    from lantai.core.attribution import resolve_attribution

    assert resolve_attribution("", {"version": 1, "request_session_id": "A"})["session_id"] == "A"
    assert resolve_attribution(" ", None)["session_id"] is None
    assert resolve_attribution("B", None)["attribution_state"] == "explicit"
    assert resolve_attribution("", None)["attribution_state"] == "missing"


@pytest.mark.parametrize(
    "value",
    [
        {"version": 2},
        {"version": True},
        {"request_session_id": "bad\nline"},
        {"request_id": "x" * 201},
        {"request_session_id": "x" * 129},
        {"user_id": "admin"},
        [],
    ],
)
def test_context_rejects_invalid_or_identity_fields(value):
    from lantai.core.attribution import Attribution

    with pytest.raises((ValidationError, ValueError)):
        Attribution.model_validate(value)


def test_mcp_namespace_is_separate_from_business_arguments():
    from lantai.core.attribution import ATTRIBUTION_META_KEY, parse_mcp_attribution

    assert parse_mcp_attribution(None) is None
    assert parse_mcp_attribution({"other": "ignored"}) is None
    got = parse_mcp_attribution(
        {ATTRIBUTION_META_KEY: {"version": 1, "request_session_id": "session-A"}}
    )
    assert got == {"version": 1, "request_session_id": "session-A"}
    with pytest.raises(ValueError):
        parse_mcp_attribution({ATTRIBUTION_META_KEY: {"version": 2}})
