"""Real SQLite smoke coverage for explicit, non-destructive candidate maintenance."""

import json
import sqlite3

import pytest

from scripts.reconcile_knowledge_candidates import apply_plan, build_plan, row_hash


@pytest.fixture
def maintenance_db(tmp_path):
    con = sqlite3.connect(tmp_path / "maintenance.db")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE memoryitem(id TEXT PRIMARY KEY,content TEXT,role TEXT,status TEXT,
          tenant_id TEXT,user_id TEXT,agent_id TEXT,session_id TEXT,namespace TEXT,
          domain TEXT,lane TEXT,source_ids TEXT,provenance TEXT,version INTEGER,
          lifecycle_status TEXT,superseded_by TEXT,superseded_at TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE memorycheckpoint(id TEXT PRIMARY KEY,memory_id TEXT,version INTEGER,
          "before" TEXT,"after" TEXT,proposal_id TEXT,trigger TEXT,created_at TEXT);
        CREATE TABLE memoryedge(id TEXT PRIMARY KEY,source_memory_id TEXT,target_memory_id TEXT,
          relation TEXT,confidence REAL,reason TEXT,created_at TEXT,
          tenant_id TEXT,user_id TEXT,agent_id TEXT,session_id TEXT);
        CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED,content);
        """
    )
    for mid, source in [("candidate-a", "source-a"), ("candidate-b", "source-b")]:
        con.execute(
            "INSERT INTO memoryitem VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid,
                "verified same content",
                "BELIEF",
                "candidate",
                None,
                "owner",
                None,
                None,
                "default",
                "user",
                "general",
                json.dumps([source]),
                "{}",
                1,
                "active",
                None,
                None,
                "2026-11-01 00:00:00",
                "2026-11-01 00:00:00",
            ),
        )
    con.commit()
    yield con
    con.close()


def test_preview_and_apply_preserve_original_text_and_sources(maintenance_db):
    con = maintenance_db
    plan = build_plan(con)
    assert plan["candidate_count"] == 2
    assert len(plan["groups"]) == 1
    assert con.execute("SELECT COUNT(*) FROM memorycheckpoint").fetchone()[0] == 0
    result = apply_plan(con, plan, expected_sha256=plan["plan_sha256"])
    assert result["changed"] == 2
    assert result["remaining_candidate_representatives"] == 1
    rows = [dict(row) for row in con.execute("SELECT * FROM memoryitem ORDER BY id")]
    assert [row["content"] for row in rows] == ["verified same content"] * 2
    assert rows[0]["status"] == "candidate"
    assert rows[0]["lifecycle_status"] == "candidate"
    assert json.loads(rows[0]["source_ids"]) == ["source-a", "source-b"]
    assert rows[1]["status"] == "consolidated"
    assert rows[1]["superseded_by"] == "candidate-a"
    assert all(row["version"] == 2 for row in rows)
    assert con.execute("SELECT COUNT(*) FROM memorycheckpoint").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM memoryedge").fetchone()[0] == 1
    assert con.execute("SELECT user_id FROM memoryedge").fetchone()[0] == "owner"
    before = json.loads(
        con.execute(
            'SELECT "before" FROM memorycheckpoint WHERE memory_id=?', ("candidate-b",)
        ).fetchone()[0]
    )
    assert before["status"] == "candidate" and before["source_ids"] == ["source-b"]
    with pytest.raises(ValueError, match="already applied"):
        apply_plan(con, plan, expected_sha256=plan["plan_sha256"])


def test_stale_row_rejects_entire_plan(maintenance_db):
    con = maintenance_db
    plan = build_plan(con)
    con.execute("UPDATE memoryitem SET version=2 WHERE id='candidate-b'")
    con.commit()
    with pytest.raises(ValueError, match="changed since preview"):
        apply_plan(con, plan, expected_sha256=plan["plan_sha256"])
    assert con.execute("SELECT COUNT(*) FROM memorycheckpoint").fetchone()[0] == 0
    assert (
        con.execute("SELECT COUNT(*) FROM memoryitem WHERE status='candidate'").fetchone()[0] == 2
    )


def test_reviewed_existing_result_closes_group_without_rewriting_target(maintenance_db):
    con = maintenance_db
    con.execute(
        "INSERT INTO memoryitem SELECT 'result',content,role,'active',tenant_id,user_id,agent_id,session_id,namespace,domain,lane,?,provenance,version,lifecycle_status,NULL,NULL,created_at,updated_at FROM memoryitem WHERE id='candidate-a'",
        (json.dumps(["candidate-a"]),),
    )
    con.commit()
    target = dict(con.execute("SELECT * FROM memoryitem WHERE id='result'").fetchone())
    reviewed = {
        "links": {
            "candidate-a": {
                "target_id": "result",
                "target_hash": row_hash(target),
                "reason": "Verified existing applied proposal and source link",
            }
        }
    }
    plan = build_plan(con, reviewed)
    result = apply_plan(con, plan, expected_sha256=plan["plan_sha256"])
    assert result["remaining_candidate_representatives"] == 0
    assert result["linked_groups"] == 1
    assert (
        con.execute("SELECT COUNT(*) FROM memoryitem WHERE status='consolidated'").fetchone()[0]
        == 2
    )
    assert dict(con.execute("SELECT * FROM memoryitem WHERE id='result'").fetchone()) == target


def test_plan_hash_and_target_evidence_are_checked(maintenance_db):
    con = maintenance_db
    plan = build_plan(con)
    with pytest.raises(ValueError, match="fingerprint"):
        apply_plan(con, plan, expected_sha256="wrong")
    reviewed = {
        "links": {"candidate-a": {"target_id": "missing", "target_hash": "x", "reason": "review"}}
    }
    with pytest.raises(ValueError, match="target missing"):
        build_plan(con, reviewed)


def test_ownership_and_role_split_groups(maintenance_db):
    con = maintenance_db
    con.execute("UPDATE memoryitem SET user_id='other-owner' WHERE id='candidate-b'")
    con.commit()
    assert len(build_plan(con)["groups"]) == 2
    con.execute("UPDATE memoryitem SET user_id='owner',role='RULE' WHERE id='candidate-b'")
    con.commit()
    assert len(build_plan(con)["groups"]) == 2
