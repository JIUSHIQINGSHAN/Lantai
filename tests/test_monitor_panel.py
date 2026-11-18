"""Arena monitor regression cases ported to the accepted ADR-0045 interface.

The alternate arena used /monitor/snapshot without the current Principal scope.
Keep its aggregate/queue/worker smoke coverage against the maintained API and
real SQLite, rather than reintroducing the obsolete global-data endpoint.
"""

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import func, select

from lantai.api.routes_monitor import router
from lantai.core.auth import Principal, get_current_user
from lantai.core.scheduler import worker_staleness
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal
from lantai.ops.monitor import build_monitor_snapshot
from lantai.services.work_item_service import worker_schedule_specs


def _seed(session):
    for owner in ("arena-alice", "arena-bob"):
        session.add(
            MemoryItem(
                id=f"arena-{owner}",
                key=f"fact/{owner}",
                content="Synthetic monitor fixture",
                user_id=owner,
                lane="general",
                status="active",
                decay_class="episodic",
            )
        )
        session.add(
            MemoryCandidate(
                id=f"arena-cand-{owner}",
                document_id=f"arena-doc-{owner}",
                summary="Synthetic pending candidate",
                user_id=owner,
                status="pending_review",
            )
        )
        session.add(
            MemoryProposal(
                id=f"arena-prop-{owner}",
                proposal_type="add",
                reason="Synthetic pending proposal",
                user_id=owner,
                status="pending",
                confidence=0.8,
            )
        )
    session.commit()


def test_arena_snapshot_keeps_real_db_aggregation_and_is_read_only(param_env):
    session_factory, _ = param_env
    with session_factory() as session:
        _seed(session)
        models = (MemoryItem, MemoryCandidate, MemoryProposal)
        before = [session.exec(select(func.count()).select_from(m)).one() for m in models]
        snapshot = build_monitor_snapshot(session, include_quality=False)
        after = [session.exec(select(func.count()).select_from(m)).one() for m in models]
    assert before == after == [2, 2, 2]
    assert snapshot["memories"]["total"] == 2
    assert snapshot["memories"]["by_lane"]["general"] == 2
    assert snapshot["review"]["candidates_pending_review"] == 2
    assert snapshot["review"]["proposals_pending"] == 2
    assert snapshot["storage"]["table_rows"]["memoryitem"] == 2
    assert {"pipeline", "dependency", "scheduler", "alerts", "requests"} <= snapshot.keys()


def test_arena_snapshot_does_not_reintroduce_cross_owner_counts(param_env):
    session_factory, _ = param_env
    with session_factory() as session:
        _seed(session)
        snapshot = build_monitor_snapshot(
            session,
            include_quality=False,
            principal=Principal(user_id="arena-alice", allowed_lanes=["general"]),
        )
    assert snapshot["memories"]["total"] == 1
    assert snapshot["review"]["candidates_pending_review"] == 1
    assert snapshot["review"]["proposals_pending"] == 1
    assert snapshot["process"]["pid"] is None


def test_arena_worker_interval_and_overdue_use_shared_policy():
    specs = worker_schedule_specs()
    assert {"ingest", "evolve", "forgetting", "digest", "reflect", "autodream"} <= specs.keys()
    assert all(spec["seconds"] > 0 for spec in specs.values())
    now = utcnow()
    old = worker_staleness(
        "ingest",
        period_seconds=3600,
        last_run=now - timedelta(hours=5),
        now=now,
    )
    assert old["overdue"] and old["critical"]
    new = worker_staleness(
        "digest",
        period_seconds=86400,
        last_run=None,
        process_started_at=now,
        now=now,
    )
    assert not new["overdue"]


def test_arena_http_view_uses_current_authenticated_endpoint(param_env):
    session_factory, _ = param_env
    with session_factory() as session:
        _seed(session)
    app = FastAPI()
    app.include_router(router)
    principal = Principal(user_id="arena-alice", allowed_lanes=["general"])
    app.dependency_overrides[get_current_user] = lambda: principal
    with TestClient(app) as client:
        response = client.get("/monitor/overview?quality=false")
        assert response.status_code == 200
        assert response.json()["memories"]["total"] == 1
        assert response.json()["process"]["pid"] is None
        assert client.get("/monitor/snapshot").status_code == 404
