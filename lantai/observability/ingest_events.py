"""Transactional, zero-body ingestion stage records. No ambient request context."""

from contextlib import contextmanager

from sqlmodel import select

from lantai.core.attribution import Attribution, resolve_attribution
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import IngestEvent
from lantai.storage import db

OUTCOMES = frozenset(
    {
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
    }
)
REASONS = frozenset(
    {
        "ingest_error",
        "input_rejected",
        "channel_disabled",
        "buffered",
        "proposal_pending",
        "proposal_rejected",
        "proposal_discarded",
        "proposal_unavailable",
        "candidate_pending",
        "candidate_rejected",
        "candidate_expired",
        "deduplicated",
        "proposal_applied",
        "post_commit_error",
        "dispatch_error",
    }
)


def start_ingest_event(
    *,
    channel: str,
    user_id: str = "default",
    tenant_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    attribution: dict | Attribution | None = None,
    event_id: str | None = None,
) -> str:
    """Persist reception before business effects; failure prevents the write.

    event_id is internal and is reused by the asynchronous worker. request_id is
    never a uniqueness or deduplication key.
    """
    if channel not in {"dialogue", "add", "raw"}:
        raise ValueError("invalid ingest channel")
    context = resolve_attribution(session_id, attribution)
    with db.get_session() as s:
        if event_id:
            old = s.get(IngestEvent, event_id)
            if (
                old is None
                or old.channel != channel
                or old.user_id != (user_id or "default")
                or old.tenant_id != tenant_id
                or old.agent_id != agent_id
                or old.session_id != context["session_id"]
                or old.request_session_id != context["request_session_id"]
                or old.request_id != context["request_id"]
            ):
                raise ValueError("ingest event does not match request")
            return old.id
        row = IngestEvent(
            id=new_id("ing"),
            channel=channel,
            user_id=user_id or "default",
            tenant_id=tenant_id,
            agent_id=agent_id,
            **context,
        )
        s.add(row)
        identifier = row.id
        s.commit()
        return identifier


def record_ingest_outcome(
    session,
    *,
    event_ids: list[str] | None = None,
    candidate_id: str | None = None,
    proposal_id: str | None = None,
    memory_id: str | None = None,
    outcome: str,
    reason_code: str | None = None,
) -> int:
    """Attach a committed stage in the caller's transaction, never commit here.

    Explicit event IDs attach a newly created entity. Later steps locate those
    same inbound events by candidate/proposal ID. Missing historical associations
    are legitimate; explicit missing event IDs are an error, never silent success.
    """
    if outcome not in OUTCOMES or (reason_code is not None and reason_code not in REASONS):
        raise ValueError("invalid ingest outcome or reason code")
    if event_ids is not None:
        if not event_ids:
            return 0
        ids = set(event_ids)
        rows = session.exec(select(IngestEvent).where(IngestEvent.id.in_(ids))).all()
        if len(rows) != len(ids):
            raise RuntimeError("ingest event association missing")
    elif proposal_id:
        rows = session.exec(select(IngestEvent).where(IngestEvent.proposal_id == proposal_id)).all()
    elif candidate_id:
        rows = session.exec(
            select(IngestEvent).where(IngestEvent.candidate_id == candidate_id)
        ).all()
    else:
        return 0
    for row in rows:
        row.outcome = outcome
        row.reason_code = reason_code
        row.updated_at = utcnow()
        if candidate_id is not None:
            row.candidate_id = candidate_id
        if proposal_id is not None:
            row.proposal_id = proposal_id
        if memory_id is not None:
            row.memory_id = memory_id
        session.add(row)
    # Force errors into the same transaction as the business write. Swallowing
    # an event error would leave a successful write without its required ledger.
    session.flush()
    return len(rows)


def candidate_outcome(status: str) -> str:
    return {"pending_review": "pending", "rejected": "rejected"}.get(status, "candidate")


def record_ingest_exception(
    event_ids: list[str], *, rejected: bool = False, reason_code: str | None = None
) -> bool:
    """Record a caught attempt failure, preserving any already committed effect.

    Returns whether business effects have committed. A process disappearing leaves
    processing as unknown; this function only runs for an observed exception.
    """
    committed = False
    with db.get_session() as s:
        rows = s.exec(select(IngestEvent).where(IngestEvent.id.in_(event_ids))).all()
        if len(rows) != len(set(event_ids)):
            raise RuntimeError("ingest event association missing")
        for row in rows:
            has_effect = bool(row.candidate_id or row.proposal_id or row.memory_id)
            committed |= has_effect
            if has_effect:
                # Do not turn a durable candidate or applied memory into failure.
                row.reason_code = "post_commit_error"
            else:
                row.outcome = "rejected" if rejected else "failed"
                row.reason_code = reason_code or ("input_rejected" if rejected else "ingest_error")
            row.updated_at = utcnow()
            s.add(row)
        s.commit()
    return committed


@contextmanager
def ingest_attempt(event_ids: list[str]):
    """Observed errors are explicit; accounting failures are never swallowed."""
    try:
        yield
    except Exception as exc:
        record_ingest_exception(event_ids, rejected=isinstance(exc, ValueError))
        raise
