"""Real SQLite cleanup keeps open work and recent changes (no body snapshots)."""

from datetime import timedelta

from sqlmodel import select

from lantai.core.time import utcnow
from lantai.models.tables import IngestEvent


def test_terminal_retention_uses_updated_at_and_keeps_pending(param_env, monkeypatch):
    from lantai.core.settings import settings
    from lantai.observability.retention import prune_all, retention_stats

    factory, _ = param_env
    now = utcnow()
    monkeypatch.setattr(settings, "INGEST_EVENT_RETENTION_DAYS", 2)
    old = now - timedelta(days=3)
    with factory() as s:
        for ident, outcome, updated in (
            ("old", "created", old),
            ("recent-update", "updated", now),
            ("boundary", "rejected", now - timedelta(days=2)),
            ("open", "pending", old),
            ("processing", "processing", old),
        ):
            s.add(
                IngestEvent(
                    id=ident,
                    channel="dialogue",
                    created_at=old,
                    updated_at=updated,
                    outcome=outcome,
                )
            )
        s.commit()
    assert prune_all(now)["ingest_event"] == 1
    assert prune_all(now)["ingest_event"] == 0
    with factory() as s:
        assert set(s.exec(select(IngestEvent.id))) == {
            "recent-update",
            "boundary",
            "open",
            "processing",
        }
    assert retention_stats()["ingest_event"]["days"] == 2
