"""摄取监控：真实 SQLite 的阶段、边界与会话反例（不 mock 内部逻辑）。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal, RetrievalEvent
from lantai.ops.monitor import collect_ingest_liveness, evaluate_alerts

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
RECENT = NOW - timedelta(minutes=5)
OWNER = {"tenant_id": "tenant-A", "user_id": "user-A", "agent_id": "agent-A"}


@pytest.fixture()
def sqlite_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _read(rid, session_id="A", *, created_at=RECENT, **kw):
    return RetrievalEvent(
        id=rid,
        trace_id=rid,
        query_text="private query must not reach the snapshot",
        query_norm_hash=rid,
        param_snapshot_hash="snapshot",
        created_at=created_at,
        session_id=session_id,
        **(OWNER | kw),
    )


def _item(mid, session_id="A", *, created_at=RECENT, **kw):
    return MemoryItem(
        id=mid,
        content="private content must not reach the snapshot",
        created_at=created_at,
        updated_at=created_at,
        session_id=session_id,
        **(OWNER | kw),
    )


def _candidate(cid, session_id="A", *, status="new", created_at=RECENT, **kw):
    return MemoryCandidate(
        id=cid,
        document_id="doc",
        summary="private candidate must not reach the snapshot",
        session_id=session_id,
        status=status,
        created_at=created_at,
        **(OWNER | kw),
    )


def _proposal(pid, session_id="A", *, status="pending", created_at=RECENT, **kw):
    return MemoryProposal(
        id=pid,
        proposal_type="add",
        session_id=session_id,
        status=status,
        created_at=created_at,
        **(OWNER | kw),
    )


def _live(session, *rows, **kw):
    session.add_all(rows)
    session.commit()
    return collect_ingest_liveness(session, now=NOW, **kw)


def _alerts(live):
    snapshot = {
        "ingest_liveness": live,
        "security": {"loopback": True, "api_key_configured": True},
        "dependency": {"llm": {"configured": True}},
    }
    return [a for a in evaluate_alerts(snapshot) if a["id"].startswith("ingest_")]


def test_reads_without_expected_ingest_are_insufficient_not_broken(sqlite_session):
    live = _live(sqlite_session, _read("read"))
    assert live["judgment"] == "insufficient_evidence"
    assert live["expected_ingest_evidence"] is False
    assert live["sessions"]["read_without_received"] == 1
    assert all(a["severity"] == "info" for a in _alerts(live))


@pytest.mark.parametrize("status", ["new", "fastpath", "gated", "pending_review", "rejected"])
def test_candidate_is_received_not_applied(sqlite_session, status):
    live = _live(sqlite_session, _read("read"), _candidate("cand", status=status))
    assert live["judgment"] == "received"
    assert live["stages"]["candidate"]["count"] == 1
    assert live["stages"]["candidate"]["by_status"][status] == 1
    assert live["sessions"]["read_with_received"] == 1
    assert live["sessions"]["read_with_applied"] == 0
    assert live["writes_session_24h"] == 0
    assert not any(a["severity"] == "critical" for a in _alerts(live))


def test_other_session_cannot_cover_current_session(sqlite_session):
    live = _live(sqlite_session, _read("read-A"), _candidate("cand-A"), _item("item-B", "B"))
    assert live["judgment"] == "received"
    assert live["writes_session_24h"] == 1
    assert live["sessions"]["read_with_applied"] == 0
    assert live["sessions"]["read_with_received"] == 1


@pytest.mark.parametrize("dimension", ["tenant_id", "user_id", "agent_id"])
def test_same_session_label_different_actor_does_not_cover_read(sqlite_session, dimension):
    live = _live(
        sqlite_session,
        _read("read-A"),
        _candidate("cand-A"),
        _item("other-actor", **{dimension: "other"}),
    )
    assert live["sessions"]["read_with_applied"] == 0
    assert live["judgment"] == "received"


def test_legacy_null_actor_is_only_partial_evidence(sqlite_session):
    live = _live(sqlite_session, _read("read"), _candidate("legacy", agent_id=None))
    assert live["sessions"]["read_with_received"] == 0
    assert live["sessions"]["read_with_partial_received"] == 1
    assert live["judgment"] == "received"


@pytest.mark.parametrize("sid", [None, "", " \t\r\n ", "\u3000"])
def test_missing_session_is_unknown_and_never_inferred_background(sqlite_session, sid):
    live = _live(sqlite_session, _read("read"), _item("unknown", sid))
    assert live["writes_session_24h"] == 0
    assert live["writes_background_24h"] == 1
    assert live["items_unknown_session_24h"] == 1
    assert (
        live["count_labels"]["writes_background_24h"] == "session 未知的 item 行（不推断后台来源）"
    )
    assert live["judgment"] == "insufficient_evidence"
    assert not any(a["severity"] in {"high", "critical"} for a in _alerts(live))


def test_half_open_window_and_trim_apply_to_all_stages(sqlite_session):
    start = NOW - timedelta(hours=24)
    rows = []
    for name, stamp in (
        ("before", start - timedelta(microseconds=1)),
        ("start", start),
        ("recent", RECENT),
        ("end", NOW),
        ("future", NOW + timedelta(hours=1)),
    ):
        rows.extend(
            [
                _read(f"read-{name}", "  A\t", created_at=stamp),
                _item(f"item-{name}", " A ", created_at=stamp),
                _candidate(f"cand-{name}", "\tA\n", created_at=stamp),
                _proposal(f"prop-{name}", " A ", created_at=stamp),
            ]
        )
    rows.extend([_read("blank", " \t"), _read("noise", is_system_noise=True)])
    live = _live(sqlite_session, *rows)
    assert live["ingest_conv_reads_24h"] == 2
    assert live["writes_session_24h"] == 2
    assert live["stages"]["candidate"]["count"] == 2
    assert live["stages"]["proposal"]["count"] == 2
    assert live["sessions"]["read"] == 1
    assert live["window_start"].startswith("2026-11-16T18:00:00")
    assert live["window_end"].startswith("2026-11-17T18:00:00")


@pytest.mark.parametrize("status", ["candidate", "archived", "consolidated"])
def test_non_active_item_is_not_applied(sqlite_session, status):
    live = _live(sqlite_session, _read("read"), _item("item", status=status))
    assert live["writes_session_24h"] == 1  # 兼容旧键仍统计 item 行，另列状态。
    assert live["stages"]["item"]["by_status"][status] == 1
    assert live["sessions"]["read_with_applied"] == 0
    assert live["judgment"] == "received"


@pytest.mark.parametrize("status", ["pending", "approved", "rejected"])
def test_proposal_decision_is_not_apply(sqlite_session, status):
    live = _live(sqlite_session, _read("read"), _proposal("prop", status=status))
    assert live["judgment"] == "received"
    assert live["sessions"]["read_with_applied"] == 0
    assert live["stages"]["proposal"]["by_status"][status] == 1


def test_applied_proposal_requires_nonfuture_applied_timestamp(sqlite_session):
    live = _live(
        sqlite_session,
        _read("read"),
        _proposal("prop", status="applied", applied_at=NOW + timedelta(seconds=1)),
    )
    assert live["sessions"]["read_with_applied"] == 0
    prop = sqlite_session.get(MemoryProposal, "prop")
    prop.applied_at = RECENT
    sqlite_session.add(prop)
    sqlite_session.commit()
    live = collect_ingest_liveness(sqlite_session, now=NOW)
    assert live["sessions"]["read_with_applied"] == 1
    assert live["judgment"] == "applied"


def test_read_only_session_is_not_masked_by_another_applied_read_session(sqlite_session):
    live = _live(sqlite_session, _read("read-A"), _read("read-B", "B"), _item("item-B", "B"))
    assert live["sessions"]["read"] == 2
    assert live["sessions"]["read_with_applied"] == 1
    assert live["sessions"]["read_without_received"] == 1
    assert live["judgment"] == "insufficient_evidence"


def test_owner_filter_applies_to_candidate_and_proposal(sqlite_session):
    principal = Principal(user_id="user-A", role="user")
    live = _live(
        sqlite_session,
        _read("read"),
        _candidate("cand-A"),
        _candidate("cand-B", user_id="user-B"),
        _proposal("prop-A"),
        _proposal("prop-B", user_id="user-B"),
        principal=principal,
    )
    assert live["stages"]["candidate"]["count"] == 1
    assert live["stages"]["proposal"]["count"] == 1


def test_optional_event_table_missing_is_explicit_not_silent_green(sqlite_session):
    sqlite_session.execute(text("DROP TABLE IF EXISTS ingest_event"))
    live = _live(sqlite_session, _read("read"), _candidate("candidate"))
    assert live["stages"]["event"]["available"] is False
    assert live["stages"]["event"]["unavailable_reason"] == "table_missing"
    assert live["judgment"] == "received"


def test_incompatible_event_schema_is_explicit_and_fallback_still_works(sqlite_session):
    sqlite_session.execute(text("DROP TABLE IF EXISTS ingest_event"))
    sqlite_session.execute(text("CREATE TABLE ingest_event (id TEXT PRIMARY KEY)"))
    live = _live(sqlite_session, _read("read"), _candidate("candidate"))
    assert live["stages"]["event"]["available"] is False
    assert live["stages"]["event"]["unavailable_reason"] == "schema_incompatible"
    assert live["stages"]["candidate"]["count"] == 1
    assert live["judgment"] == "received"


@pytest.mark.parametrize("judgment", ["broken", "background_only"])
def test_legacy_liveness_judgments_without_expected_evidence_only_info(judgment):
    alerts = _alerts(
        {
            "judgment": judgment,
            "ingest_conv_reads_24h": 30,
            "writes_session_24h": 0,
            "writes_background_24h": 40,
        }
    )
    assert alerts
    assert {a["severity"] for a in alerts} == {"info"}
    text_output = str(alerts)
    assert "每句新话" not in text_output
    assert "全部来自后台" not in text_output


def test_snapshot_only_contains_bounded_aggregate_values(sqlite_session):
    live = _live(sqlite_session, _read("read"), _candidate("cand"))
    rendered = str(live)
    for private in ("private", "tenant-A", "user-A", "agent-A", "read", "cand"):
        if private in {"read", "cand"}:
            continue  # 聚合键包含 read/candidate 单词，其他值与正文不得出现。
        assert private not in rendered
    assert not any(isinstance(value, list) for value in live.values())
