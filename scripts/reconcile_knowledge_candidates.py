"""Preview/apply an explicit, reversible consolidation of ordinary candidates.

This offline maintenance command never promotes or deletes memory. Run with the
service stopped and a verified cold backup. A reviewed mapping can close groups
already represented by an active memory; otherwise one candidate remains visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

GROUP_FIELDS = (
    "tenant_id",
    "user_id",
    "agent_id",
    "session_id",
    "namespace",
    "domain",
    "lane",
    "role",
    "content",
)
JSON_FIELDS = {"structure", "provenance", "tags", "source_ids", "promotion_trace"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_hash(row: dict) -> str:
    return hashlib.sha256(_json(row).encode()).hexdigest()


def _decoded(row):
    return {
        key: json.loads(value) if key in JSON_FIELDS and isinstance(value, str) else value
        for key, value in row.items()
    }


def build_plan(connection: sqlite3.Connection, reviewed: dict | None = None) -> dict:
    """Read-only grouping; links require a reviewed target fingerprint and provenance."""
    connection.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM memoryitem WHERE status='candidate' ORDER BY created_at,id"
        )
    ]
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(key) for key in GROUP_FIELDS)].append(row)
    mappings = (reviewed or {}).get("links", {})
    known_ids = {row["id"] for row in rows}
    if set(mappings) - known_ids:
        raise ValueError("reviewed representative no longer candidate")
    operations = []
    for values in groups.values():
        representative = values[0]
        group_ids = {row["id"] for row in values}
        supplied = [mid for mid in group_ids if mid in mappings]
        if supplied and supplied != [representative["id"]]:
            raise ValueError("reviewed mapping must name the oldest representative")
        mapping = mappings.get(representative["id"])
        target = None
        if mapping:
            if not str(mapping.get("reason", "")).strip():
                raise ValueError("reviewed mapping requires reason")
            found = connection.execute(
                "SELECT * FROM memoryitem WHERE id=?", (mapping["target_id"],)
            ).fetchone()
            if found is None:
                raise ValueError("reviewed target missing")
            target = dict(found)
            if target["status"] != "active" or row_hash(target) != mapping["target_hash"]:
                raise ValueError("reviewed active target changed")
            if any(
                target.get(k) != representative.get(k)
                for k in ("tenant_id", "user_id", "namespace", "domain", "lane")
            ):
                raise ValueError("reviewed target ownership or classification differs")
            # This is an explicit reviewed mapping, not an automatic semantic match.
            # The whole-row fingerprint includes the exact target content reviewed
            # by the operator; source_ids alone never establish equivalence.
            # Same text may have both BELIEF and RULE candidates. Require a real
            # existing source link to one such row, not just a matching title.
            source_ids = json.loads(target.get("source_ids") or "[]")
            anchors = [
                row
                for row in rows
                if row["id"] in source_ids and row["content"] == representative["content"]
            ]
            if not anchors:
                raise ValueError("reviewed target lacks matching candidate provenance")
        merged_sources = sorted(
            {mid for row in values for mid in json.loads(row.get("source_ids") or "[]")} - group_ids
        )
        operations.append(
            {
                "representative_id": representative["id"],
                "role": representative["role"],
                "content_sha256": hashlib.sha256(representative["content"].encode()).hexdigest(),
                "rows": [{"id": row["id"], "hash": row_hash(row)} for row in values],
                "merged_source_ids": merged_sources,
                "target_id": target["id"] if target else None,
                "target_hash": row_hash(target) if target else None,
                "reason": mapping["reason"]
                if mapping
                else "Exact duplicate candidate consolidation; representative remains pending evidence review",
            }
        )
    plan = {"format": 1, "candidate_count": len(rows), "groups": operations}
    plan["plan_sha256"] = row_hash(plan)
    return plan


def _checkpoint(connection, before, after, batch, now):
    connection.execute(
        'INSERT INTO memorycheckpoint(id,memory_id,version,"before","after",proposal_id,trigger,created_at) VALUES (?,?,?,?,?,?,?,?)',
        (
            "ckpt_" + uuid.uuid4().hex,
            before["id"],
            after["version"],
            _json(_decoded(before)),
            _json(_decoded(after)),
            None,
            "candidate_reconcile:" + batch,
            now,
        ),
    )


def apply_plan(connection: sqlite3.Connection, plan: dict, *, expected_sha256: str) -> dict:
    """Compare every affected row and commit metadata+edges+checkpoints atomically."""
    check = dict(plan)
    actual = check.pop("plan_sha256")
    if actual != expected_sha256 or row_hash(check) != actual:
        raise ValueError("plan fingerprint mismatch")
    connection.row_factory = sqlite3.Row
    batch = actual[:24]
    trigger = "candidate_reconcile:" + batch
    prior = connection.execute(
        "SELECT COUNT(*) FROM memorycheckpoint WHERE trigger=?", (trigger,)
    ).fetchone()[0]
    if prior:
        raise ValueError("plan was already applied; inspect receipt instead of replaying")
    connection.execute("BEGIN IMMEDIATE")
    changed = []
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    try:
        loaded = {}
        for group in plan["groups"]:
            for ref in group["rows"]:
                row = connection.execute(
                    "SELECT * FROM memoryitem WHERE id=?", (ref["id"],)
                ).fetchone()
                if (
                    row is None
                    or row_hash(dict(row)) != ref["hash"]
                    or row["status"] != "candidate"
                ):
                    raise ValueError("candidate changed since preview")
                if connection.execute(
                    "SELECT COUNT(*) FROM memory_fts WHERE memory_id=?", (ref["id"],)
                ).fetchone()[0]:
                    raise ValueError("candidate unexpectedly indexed; stop and inspect")
                loaded[ref["id"]] = dict(row)
            if group["target_id"]:
                target = connection.execute(
                    "SELECT * FROM memoryitem WHERE id=?", (group["target_id"],)
                ).fetchone()
                if (
                    target is None
                    or row_hash(dict(target)) != group["target_hash"]
                    or target["status"] != "active"
                ):
                    raise ValueError("target changed since preview")
        for group in plan["groups"]:
            representative = group["representative_id"]
            for ref in group["rows"]:
                before = loaded[ref["id"]]
                target = group["target_id"] or (
                    representative if before["id"] != representative else None
                )
                after = dict(before)
                after.update(version=before["version"] + 1, updated_at=now)
                provenance = json.loads(before.get("provenance") or "{}")
                provenance["candidate_reconciliation"] = {
                    "batch": batch,
                    "representative_id": representative,
                    "target_id": target,
                    "reason": group["reason"],
                }
                after["provenance"] = _json(provenance)
                if target:
                    after.update(
                        status="consolidated",
                        lifecycle_status="superseded",
                        superseded_by=target,
                        superseded_at=now,
                    )
                else:
                    after.update(
                        lifecycle_status="candidate", source_ids=_json(group["merged_source_ids"])
                    )
                keys = [key for key in after if after[key] != before[key]]
                sql = (
                    "UPDATE memoryitem SET "
                    + ",".join('"' + key + '"=?' for key in keys)
                    + " WHERE id=? AND version=? AND status='candidate'"
                )
                cursor = connection.execute(
                    sql, [after[key] for key in keys] + [before["id"], before["version"]]
                )
                if cursor.rowcount != 1:
                    raise ValueError("candidate version conflict")
                _checkpoint(connection, before, after, batch, now)
                if target:
                    # Exact records have been absorbed by the representative/result.
                    # Keep originals and provenance; edge direction matches supersedes.
                    exists = connection.execute(
                        "SELECT id FROM memoryedge WHERE source_memory_id=? AND target_memory_id=? AND relation='supersedes'",
                        (target, before["id"]),
                    ).fetchone()
                    if exists is None:
                        edge = {
                            "id": "edge_" + uuid.uuid4().hex,
                            "source_memory_id": target,
                            "target_memory_id": before["id"],
                            "relation": "supersedes",
                            "confidence": 1.0,
                            "reason": trigger,
                            "created_at": now,
                            **{
                                key: before.get(key)
                                for key in ("tenant_id", "user_id", "agent_id", "session_id")
                            },
                        }
                        columns = list(edge)
                        connection.execute(
                            "INSERT INTO memoryedge("
                            + ",".join(columns)
                            + ") VALUES ("
                            + ",".join("?" for _ in columns)
                            + ")",
                            [edge[key] for key in columns],
                        )
                changed.append(
                    {
                        "id": before["id"],
                        "from": before["status"],
                        "to": after["status"],
                        "superseded_by": target,
                    }
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "ok": True,
        "batch": batch,
        "changed": len(changed),
        "remaining_candidate_representatives": sum(not g["target_id"] for g in plan["groups"]),
        "linked_groups": sum(bool(g["target_id"]) for g in plan["groups"]),
        "records": changed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--apply-sha256")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("database must already exist")
    if args.apply_sha256:
        if not args.receipt or args.receipt.exists():
            parser.error("apply needs a fresh --receipt path")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        with sqlite3.connect(args.database, timeout=30) as connection:
            result = apply_plan(connection, plan, expected_sha256=args.apply_sha256)
        args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(_json({key: value for key, value in result.items() if key != "records"}))
    else:
        if args.plan.exists():
            parser.error("refuse to overwrite an existing plan")
        reviewed = json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else {}
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            plan = build_plan(connection, reviewed)
        args.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            _json(
                {
                    "candidate_count": plan["candidate_count"],
                    "groups": len(plan["groups"]),
                    "plan_sha256": plan["plan_sha256"],
                }
            )
        )


if __name__ == "__main__":
    main()
