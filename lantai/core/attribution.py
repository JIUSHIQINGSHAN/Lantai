"""Bounded per-call attribution values, separate from identity and read scope.

Wire metadata never grants ownership. No global/ambient request state is kept here.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from lantai.core.text import normalize_session_id

ATTRIBUTION_META_KEY = "io.lantai/attribution"


class Attribution(BaseModel):
    """Optional Host labels only; authentication remains the caller's Principal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt = Field(default=1, ge=1, le=1)
    request_session_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=200)

    @field_validator("request_session_id", "request_id")
    @classmethod
    def _bounded_identifier(cls, value):
        if value is None:
            return None
        # Validate before stripping: embedded/edge line breaks are never valid labels.
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            raise ValueError("attribution identifiers must not contain control characters")
        return value.strip() or None


def attribution_dict(value) -> dict | None:
    """Validate a body/metadata envelope without reading configuration or identity."""
    if value is None:
        return None
    parsed = value if isinstance(value, Attribution) else Attribution.model_validate(value)
    return parsed.model_dump(exclude_none=True)


def parse_mcp_attribution(meta) -> dict | None:
    """Extract our namespace; unrelated metadata remains unrelated metadata."""
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("MCP _meta must be an object")
    if ATTRIBUTION_META_KEY not in meta:
        return None
    return attribution_dict(meta[ATTRIBUTION_META_KEY])


def resolve_attribution(session_id, attribution=None) -> dict:
    """Preserve explicit origin, fall back only if absent, and report conflicts.

    request_id is a correlation label, never an idempotency key. Old origin IDs
    retain the existing normalizer contract; the new envelope is strict/bounded.
    """
    origin = normalize_session_id(session_id, default=None)
    context = attribution_dict(attribution) or {}
    request_session = context.get("request_session_id")
    state: Literal["missing", "explicit", "fallback", "conflict"] = "missing"
    if origin:
        state = "conflict" if request_session and origin != request_session else "explicit"
    elif request_session:
        state = "fallback"
    return {
        "session_id": origin or request_session,
        "request_session_id": request_session,
        "request_id": context.get("request_id"),
        "attribution_state": state,
    }
