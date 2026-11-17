"""Read-only ingestion stages and scoped session correlations (ADR-0088).

Counts are evidence about stored operations, not a claim that every user turn
should have been written. No content, raw identity, or session IDs leave here.
"""

from collections import Counter
from datetime import timedelta

from sqlalchemy import and_, case, inspect, or_
from sqlmodel import func, select

from lantai.core.time import ensure_aware, iso_utc, utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal, RetrievalEvent

# SQLite trim's default is only U+0020. Match str.strip whitespace at the boundary.
_WHITESPACE = "\t\n\v\f\r \x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_STATUSES = {
    "item": {"active", "candidate", "archived", "consolidated"},
    "candidate": {"new", "fastpath", "gated", "pending_review", "rejected", "approved", "deferred"},
    "proposal": {"pending", "approved", "applied", "rejected", "cancelled"},
    "event": {
        "processing",
        "candidate",
        "created",
        "reinforced",
        "merged",
        "updated",
        "deprecated",
        "applied",
        "pending",
        "rejected",
        "failed",
    },
}
_APPLIED_OUTCOMES = {"created", "reinforced", "merged", "updated", "deprecated", "applied"}


def _session_column(column):
    return func.nullif(func.trim(column, _WHITESPACE), "")


def _scope(model, principal):
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return []
    from lantai.core.acl import viewer_of

    conditions = [or_(model.user_id == viewer_of(principal), model.user_id.is_(None))]
    tenant = getattr(principal, "tenant_id", None)
    if tenant:
        conditions.append(or_(model.tenant_id == tenant, model.tenant_id.is_(None)))
    agent = getattr(principal, "agent_id", None)
    if agent:
        conditions.append(or_(model.agent_id == agent, model.agent_id.is_(None)))
    return conditions


def _groups(session, model, *, start, end, principal, kind):
    """Select grouped scalar fields only; never materialize stored text/JSON."""
    sid = _session_column(model.session_id)
    conditions = [model.created_at >= start, model.created_at < end, *_scope(model, principal)]
    applied = None
    if kind == "read":
        state = model.is_system_noise
        conditions.extend([model.is_system_noise == False, sid.is_not(None)])  # noqa: E712
        applied = case((model.is_system_noise == True, 1), else_=0)  # noqa: E712
    else:
        state = model.outcome if kind == "event" else model.status
        if kind == "item":
            applied = case(
                (
                    and_(
                        model.status == "active", model.lifecycle_status.in_(["active", "weakened"])
                    ),
                    1,
                ),
                else_=0,
            )
        elif kind == "proposal":
            applied_window = and_(model.applied_at >= start, model.applied_at < end)
            # Applied old proposals are changes in this window even if created earlier.
            conditions = [
                or_(and_(model.created_at >= start, model.created_at < end), applied_window),
                *_scope(model, principal),
            ]
            applied = case((and_(state == "applied", applied_window), 1), else_=0)
        elif kind == "event":
            sid = func.coalesce(_session_column(model.request_session_id), sid)
            updated_window = and_(model.updated_at >= start, model.updated_at < end)
            conditions = [
                or_(and_(model.created_at >= start, model.created_at < end), updated_window),
                model.created_at < end,
                *_scope(model, principal),
            ]
            applied = case((and_(state.in_(sorted(_APPLIED_OUTCOMES)), updated_window), 1), else_=0)
        else:
            applied = case((model.id.is_(None), 1), else_=0)
    fields = [model.tenant_id, model.user_id, model.agent_id, sid, state, applied]
    statement = select(*fields, func.count(model.id)).where(*conditions).group_by(*fields)
    return session.exec(statement).all()


def _summarize(rows, kind):
    statuses = Counter()
    received = set()
    applied = set()
    marked_count = 0
    unmarked_count = 0
    for tenant, user, agent, sid, state, is_applied, count in rows:
        count = int(count)
        statuses[state if state in _STATUSES[kind] else "other"] += count
        if sid:
            marked_count += count
            key = (tenant, user, agent, sid)
            # A processing row establishes reception, never successful persistence.
            received.add(key)
            if is_applied:
                applied.add(key)
        else:
            unmarked_count += count
    return (
        {
            "available": True,
            "count": marked_count + unmarked_count,
            "with_session": marked_count,
            "without_session": unmarked_count,
            "by_status": dict(sorted(statuses.items())),
        },
        received,
        applied,
    )


def _partial_matches(read_keys, evidence_keys):
    """At most 8 projections per key; missing actors never become exact proof."""

    def projections(keys):
        output = {mask: set() for mask in range(8)}
        for tenant, user, agent, sid in keys:
            actor = (tenant, user, agent)
            for mask in range(8):
                output[mask].add((sid, *(actor[i] for i in range(3) if mask & (1 << i))))
        return output

    # Group by which actor dimensions are actually present on evidence.
    by_presence = {}
    for key in evidence_keys:
        mask = sum((1 << i) for i, value in enumerate(key[:3]) if value is not None)
        by_presence.setdefault(mask, set()).add(key)
    indexes = {mask: projections(keys) for mask, keys in by_presence.items()}
    partial = set()
    for key in read_keys - evidence_keys:
        read_mask = sum((1 << i) for i, value in enumerate(key[:3]) if value is not None)
        for evidence_mask, index in indexes.items():
            shared = read_mask & evidence_mask
            projection = (key[3], *(key[i] for i in range(3) if shared & (1 << i)))
            if projection in index[shared]:
                partial.add(key)
                break
    return partial


def collect_stages(session, *, now=None, principal=None):
    """Real SQL stage counts; missing legacy event table is explicitly reported."""
    end = ensure_aware(now or utcnow())
    start = end - timedelta(hours=24)
    reads = _groups(session, RetrievalEvent, start=start, end=end, principal=principal, kind="read")
    read_keys = {tuple(row[:4]) for row in reads}
    read_count = sum(int(row[-1]) for row in reads)
    stages = {}
    received_keys, applied_keys = set(), set()
    for kind, model in (
        ("item", MemoryItem),
        ("candidate", MemoryCandidate),
        ("proposal", MemoryProposal),
    ):
        rows = _groups(session, model, start=start, end=end, principal=principal, kind=kind)
        stages[kind], received, applied = _summarize(rows, kind)
        received_keys |= received
        applied_keys |= applied

    # Only inspect schema, never open another connection or migrate while observing.
    inspector = inspect(session.connection())
    event_info = {
        "available": False,
        "unavailable_reason": "table_missing",
        "count": 0,
        "by_status": {},
    }
    if inspector.has_table("ingest_event"):
        expected = {
            "id",
            "created_at",
            "updated_at",
            "user_id",
            "tenant_id",
            "agent_id",
            "session_id",
            "request_session_id",
            "outcome",
        }
        columns = {c["name"] for c in inspector.get_columns("ingest_event")}
        if expected <= columns:
            from lantai.models.tables import IngestEvent

            rows = _groups(
                session, IngestEvent, start=start, end=end, principal=principal, kind="event"
            )
            event_info, received, applied = _summarize(rows, "event")
            received_keys |= received
            applied_keys |= applied
        else:
            event_info["unavailable_reason"] = "schema_incompatible"
    stages["event"] = event_info
    partial = _partial_matches(read_keys, received_keys)
    exact_received = read_keys & received_keys
    exact_applied = read_keys & applied_keys
    uncovered = read_keys - exact_received - partial
    if not read_keys:
        judgment = "no_evidence"
    elif uncovered:
        judgment = "insufficient_evidence"
    elif exact_applied == read_keys:
        judgment = "applied"
    else:
        judgment = "received"
    return {
        "window_hours": 24,
        "window_start": iso_utc(start),
        "window_end": iso_utc(end),
        "ingest_conv_reads_24h": read_count,
        "writes_session_24h": stages["item"]["with_session"],
        "writes_background_24h": stages["item"]["without_session"],
        "items_unknown_session_24h": stages["item"]["without_session"],
        "count_labels": {
            "writes_session_24h": "session 标记的 item 行（不等于成功摄取次数）",
            "writes_background_24h": "session 未知的 item 行（不推断后台来源）",
        },
        "judgment": judgment,
        # A read is not an expected write. Even received events do not prove all
        # user turns should enter this pipeline. No host intent counter exists yet.
        "expected_ingest_evidence": False,
        "stages": stages,
        "sessions": {
            "read": len(read_keys),
            "read_with_received": len(exact_received),
            "read_with_partial_received": len(partial),
            "read_with_applied": len(exact_applied),
            "read_without_received": len(uncovered),
        },
        "correlation_is_identity_proof": False,
    }
