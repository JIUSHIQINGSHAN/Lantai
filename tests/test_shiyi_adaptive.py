"""拾遗公开接口回归：真实 SQLite，不替身检索、授权或打分逻辑。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lantai.cognition.shiyi import ShiyiEngine
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


def _seed(session, mid="hit", content="nebula deployment policy", **kw):
    values = dict(id=mid, content=content, user_id="alice", confidence=0.9)
    values.update(kw)
    session.add(MemoryItem(**values))
    session.commit()


@pytest.mark.parametrize(
    "query,limit", [("", 5), ("   ", 5), ("!!!", 5), ("nebula", 0), ("nebula", -1)]
)
def test_empty_request_never_replays_previous_candidates(param_env, query, limit):
    factory, _ = param_env
    with factory() as session:
        _seed(session)
        engine = ShiyiEngine(session, Principal(user_id="alice"))
        assert [r["memory"]["id"] for r in engine.remedy_search_pipeline("nebula")] == ["hit"]
        assert engine.remedy_search_pipeline(query, limit=limit) == []


@pytest.mark.parametrize("query", ["orionvectorizer", "ＯＲＩＯＮＶＥＣＴＯＲＩＺＥＲ"])
def test_long_tail_substring_recall_without_whole_token(param_env, query):
    factory, _ = param_env
    with factory() as session:
        _seed(session, content="orionvectorization deployment")
        _seed(session, "noise", "rezilauqevnoiro unrelated")
        result = ShiyiEngine(session, Principal(user_id="alice")).remedy_recall(query)
        assert [r["id"] for r in result["remedied_items"]] == ["hit"]
        assert result["remedied_items"][0]["remedy_tier"] == "subsequence"
        assert "<memory_data" in result["remedied_items"][0]["fenced_content"]


def test_unordered_single_character_overlap_is_not_evidence(param_env):
    factory, _ = param_env
    with factory() as session:
        _seed(session, content="zyxwvut")
        assert ShiyiEngine(session).remedy_recall("tuvwxyz")["remedied_items"] == []


@pytest.mark.parametrize("limit,expected", [(1, ["local"]), (2, ["local", "remote"])])
def test_domain_relaxation_only_fills_missing_slots(param_env, limit, expected):
    factory, _ = param_env
    with factory() as session:
        _seed(session, "local", "nebula", domain="user")
        _seed(session, "remote", domain="agent")
        result = ShiyiEngine(session, Principal(user_id="alice")).remedy_recall(
            "nebula deployment policy", domain="user", allow_domain_relax=True, limit=limit
        )
        assert [r["id"] for r in result["remedied_items"]] == expected
        assert result["remedied_items"][0]["remedy_tier"] == "fuzzy_tokens"


def test_decay_rollback_is_bounded_explained_and_read_only(param_env):
    factory, _ = param_env
    with factory() as session:
        _seed(session, decay_score=0.01)
        engine = ShiyiEngine(session, Principal(user_id="alice"))
        result = engine.remedy_recall("nebula deployment policy")
        item = result["remedied_items"][0]
        assert item["remedy_tier"] == "decay_boost"
        assert 0.3 <= item["confidence"] < 0.6
        assert item["metadata"]["score_before"] < 0.3
        assert item["metadata"]["original_query"] == "nebula deployment policy"
        assert not session.dirty
        session.commit()
        session.expire_all()
        assert session.get(MemoryItem, "hit").decay_score == 0.01
        projected = engine.remedy_search_pipeline("nebula deployment policy")[0]
        assert projected["memory"]["metadata"]["remedy_tier"] == "decay_boost"
        assert projected["score"] == item["confidence"]


@pytest.mark.parametrize(
    "attributes",
    [
        {"lane": "secret"},
        {"tenant_id": "other"},
        {"valid_from": utcnow() + timedelta(days=1)},
        {"valid_to": utcnow() - timedelta(days=1)},
        {"lifecycle_status": "retired"},
    ],
)
def test_relaxation_cannot_breach_current_view_or_authorization(param_env, attributes):
    factory, _ = param_env
    with factory() as session:
        _seed(session, "blocked", domain="agent", **attributes)
        _seed(session, "control", domain="agent", lane="general")
        result = ShiyiEngine(
            session, Principal(user_id="alice", allowed_lanes=["general"])
        ).remedy_recall("nebula deployment policy", domain="user", allow_domain_relax=True)
        assert [r["id"] for r in result["remedied_items"]] == ["control"]


def test_zero_confidence_is_not_replaced_with_default(param_env):
    factory, _ = param_env
    with factory() as session:
        _seed(session, confidence=0.0)
        item = ShiyiEngine(session).remedy_recall("nebula")["remedied_items"][0]
        assert item["confidence"] == 0.7


def test_scan_row_cap_reports_partial_results(param_env, monkeypatch):
    from lantai.core.settings import settings

    factory, _ = param_env
    monkeypatch.setattr(settings, "SHIYI_SCAN_BUDGET_MS", 60_000)
    with factory() as session:
        session.add_all(
            [MemoryItem(id=f"row-{i:04d}", content="nebula", user_id="alice") for i in range(513)]
        )
        session.commit()
        result = ShiyiEngine(session).remedy_recall("nebula", limit=1)
        assert result["scan_truncated"] is True
        assert result["scanned_count"] == 512
        assert [r["id"] for r in result["remedied_items"]] == ["row-0000"]


def test_budget_preserves_already_found_candidates(param_env, monkeypatch):
    from lantai.core.settings import settings

    factory, _ = param_env
    monkeypatch.setattr(settings, "SHIYI_SCAN_BUDGET_MS", 100)
    with factory() as session:
        _seed(session, "first")
        _seed(session, "second")
        engine = ShiyiEngine(session)
        ticks = iter([0.0, 0.0, 0.0, 0.2])
        monkeypatch.setattr(engine, "_now", lambda: next(ticks))
        result = engine.remedy_recall("nebula")
        assert result["scan_truncated"] is True
        assert result["scanned_count"] == 1
        assert [r["id"] for r in result["remedied_items"]] == ["first"]


@pytest.mark.parametrize("principal", [None, Principal(user_id="alice")])
def test_literal_underscore_does_not_trigger_full_scan(param_env, principal):
    factory, _ = param_env
    with factory() as session:
        _seed(session)
        result = ShiyiEngine(session, principal).remedy_recall("____")
        assert result["remedied_items"] == []
        assert result["scanned_count"] == 0


def test_bound_agent_cannot_relax_lane_even_for_shared_rows(param_env, monkeypatch):
    from lantai.core.settings import settings

    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"restricted": ["general"]})
    with factory() as session:
        _seed(session, "blocked", lane="secret", shared_scope="swarm_shared")
        _seed(session, "control", lane="general")
        result = ShiyiEngine(
            session, Principal(user_id="alice", agent_id="restricted", allowed_lanes=["secret"])
        ).remedy_recall("nebula")
        assert [r["id"] for r in result["remedied_items"]] == ["control"]


def test_decay_remedies_do_not_displace_regular_cross_domain_results(param_env):
    factory, _ = param_env
    with factory() as session:
        _seed(session, "decayed", domain="user", decay_score=0.01)
        _seed(session, "remote", domain="agent")
        engine = ShiyiEngine(session, Principal(user_id="alice"))
        result = engine.remedy_recall("nebula", domain="user", allow_domain_relax=True, limit=2)
        assert [r["id"] for r in result["remedied_items"]] == ["remote", "decayed"]
        assert [r["remedy_tier"] for r in result["remedied_items"]] == [
            "domain_relax",
            "decay_boost",
        ]
        assert engine.remedy_recall("nebula", domain="user")["remedied_items"][0]["id"] == "decayed"


@pytest.mark.parametrize("tenant", ["team-a", "team-b", None])
def test_shared_recall_still_requires_same_tenant_or_owner(param_env, tenant):
    factory, _ = param_env
    with factory() as session:
        _seed(session, "shared", user_id="bob", tenant_id=tenant, shared_scope="swarm_shared")
        _seed(session, "own", tenant_id="team-a")
        result = ShiyiEngine(session, Principal(user_id="alice", tenant_id="team-a")).remedy_recall(
            "nebula"
        )
        expected = {"own", "shared"} if tenant == "team-a" else {"own"}
        assert {r["id"] for r in result["remedied_items"]} == expected
