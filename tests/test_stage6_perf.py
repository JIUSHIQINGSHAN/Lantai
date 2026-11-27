"""Real SQLite performance invariants; no timing thresholds or mocked computation."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from lantai.core.acl import Principal
from lantai.storage.fts import _bm25_keywords, init_fts, rebuild_fts, search_fts_bm25


@pytest.fixture
def fts_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE memoryitem (id TEXT PRIMARY KEY, content TEXT, status TEXT, "
                "lane TEXT DEFAULT 'fact', domain TEXT DEFAULT 'user', user_id TEXT, "
                "tenant_id TEXT, federation_id TEXT, agent_id TEXT, shared_scope TEXT)"
            )
        )
        assert init_fts(conn.connection.driver_connection)
    try:
        yield engine
    finally:
        engine.dispose()


def seed(engine, rows):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO memoryitem(id,content,status,user_id) VALUES (:id,:c,:s,:u)"), rows
        )


def test_rebuild_fts_bounds_database_round_trips(fts_engine):
    """1,201 rows must not incur 1,201 separate SQLAlchemy executions."""
    seed(
        fts_engine,
        [{"id": str(i), "c": f"ＡＩ检索回归样本{i}", "s": "active", "u": "u"} for i in range(1201)],
    )
    inserts = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO memory_fts"):
            inserts.append(len(parameters) if executemany else 1)

    event.listen(fts_engine, "before_cursor_execute", observe)
    try:
        with Session(fts_engine) as session:
            assert rebuild_fts(session) == 1201
            session.commit()
    finally:
        event.remove(fts_engine, "before_cursor_execute", observe)
    with fts_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM memory_fts")).scalar_one() == 1201
        assert conn.execute(
            text("SELECT content FROM memory_fts WHERE memory_id='0'")
        ).scalar_one() == ("AI检索回归样本0")
    assert len(inserts) <= math.ceil(1201 / 500), f"row-at-a-time execution: {len(inserts)}"
    assert sum(inserts) == 1201
    assert max(inserts) <= 500, "index rebuild must bound its write batch"


def test_rebuild_fts_keeps_transaction_count_and_lifecycle(fts_engine):
    rows = [
        {"id": "active", "c": "ＡＩ检索", "s": "active", "u": "u"},
        {"id": "archived", "c": "归档检索", "s": "archived", "u": "u"},
        {"id": "space", "c": "  \t", "s": "active", "u": "u"},
        {"id": "empty", "c": "", "s": "active", "u": "u"},
        {"id": "null", "c": None, "s": "active", "u": "u"},
        {"id": "retracted", "c": "撤回检索", "s": "retracted", "u": "u"},
        {"id": "consolidated", "c": "巩固检索", "s": "consolidated", "u": "u"},
    ]
    seed(fts_engine, rows)
    with fts_engine.begin() as conn:
        conn.execute(text("INSERT INTO memory_fts VALUES ('old','旧索引')"))
    with Session(fts_engine) as session:
        # Preserve the existing return contract: source rows, including whitespace.
        assert rebuild_fts(session) == 3
        assert session.execute(text("SELECT count(*) FROM memory_fts")).scalar_one() == 2
        session.rollback()
    with fts_engine.connect() as conn:
        assert conn.execute(text("SELECT memory_id FROM memory_fts")).scalars().all() == ["old"]
        assert conn.execute(
            text("SELECT content FROM memoryitem WHERE id='active'")
        ).scalar_one() == ("ＡＩ检索")


def test_rebuild_fts_late_batch_failure_can_roll_back(fts_engine):
    """A real SQLite failure after the first batch must not commit a partial index."""
    seed(
        fts_engine,
        [{"id": str(i), "c": f"检索样本{i}", "s": "active", "u": "u"} for i in range(601)],
    )
    with fts_engine.begin() as conn:
        conn.execute(text("INSERT INTO memory_fts VALUES ('old','原索引')"))
        # FTS virtual tables cannot have triggers. The backing content table is
        # real SQLite storage, so this exercises a late write error without mocks.
        conn.execute(
            text(
                "CREATE TRIGGER fail_index BEFORE INSERT ON memory_fts_content "
                "WHEN NEW.c0='550' BEGIN SELECT RAISE(ABORT,'index failure'); END"
            )
        )
    with Session(fts_engine) as session:
        with pytest.raises(IntegrityError, match="constraint failed"):
            rebuild_fts(session)
        session.rollback()
    with fts_engine.connect() as conn:
        assert conn.execute(text("SELECT memory_id FROM memory_fts")).scalars().all() == ["old"]


@pytest.mark.parametrize("top_k", [1, 7, 200])
@pytest.mark.parametrize("principal", [None, Principal(user_id="u"), Principal(role="admin")])
def test_bm25_rank_matches_original_weighted_scores(fts_engine, principal, top_k):
    """Compare actual top-k IDs and floats, including score ties and owner filtering."""
    seed(
        fts_engine,
        [
            {
                "id": str(i),
                "c": ("检索性能 SQLite " * (1 + i % 5)) + f"样本{i}",
                "s": "active",
                "u": "other" if i % 4 == 0 else "u",
            }
            for i in range(120)
        ],
    )
    with Session(fts_engine) as session:
        rebuild_fts(session)
        session.commit()
        raw = session.connection().connection.driver_connection
        query = " OR ".join('"' + t + '"' for t in _bm25_keywords("检索性能 SQLite"))
        owner = "" if principal is None or principal.is_admin else " AND m.user_id='u'"
        reference = raw.execute(
            "SELECT f.memory_id, bm25(memory_fts,10.0,5.0) AS score FROM memory_fts f "
            "JOIN memoryitem m ON f.memory_id=m.id WHERE f.memory_fts MATCH ?"
            + owner
            + " ORDER BY score LIMIT ?",
            (query, top_k),
        ).fetchall()
        assert (
            search_fts_bm25(raw, "检索性能 SQLite", top_k=top_k, principal=principal) == reference
        )


def test_bm25_uses_fts_order_without_external_sort(fts_engine):
    """The real query must use FTS rank ordering rather than a temporary sort tree."""
    seed(fts_engine, [{"id": "one", "c": "SQLite性能检索", "s": "active", "u": "u"}])
    with Session(fts_engine) as session:
        rebuild_fts(session)
        session.commit()
        raw = session.connection().connection.driver_connection
        statements = []
        raw.set_trace_callback(statements.append)
        try:
            assert search_fts_bm25(raw, "SQLite性能检索", principal=Principal(user_id="u"))
        finally:
            raw.set_trace_callback(None)
        actual = next(s for s in statements if "SELECT f.memory_id" in s)
        plan = raw.execute("EXPLAIN QUERY PLAN " + actual).fetchall()
        assert not any("TEMP B-TREE FOR ORDER BY" in row[-1] for row in plan), plan


def load_benchmark():
    spec = importlib.util.spec_from_file_location(
        "stage6_perf", Path(__file__).resolve().parents[1] / "scripts/perf_stage6.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stage6_benchmark_real_subprocess_smoke(tmp_path):
    """Exercise cold startup plus real Chroma, index rebuild and concurrent retrieval."""
    mod = load_benchmark()
    report = tmp_path / "baseline.json"
    assert (
        mod.main(
            [
                "--scale",
                "40",
                "--iterations",
                "2",
                "--repeats",
                "1",
                "--workers",
                "2",
                "--out",
                str(report),
            ]
        )
        == 0
    )
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["cold"][0]["mcp_import_ms"] > 0
    assert result["cold"][0]["db_init_ms"] > 0
    cold = result["cold"][0]
    assert cold["spawn_to_ready_ms"] >= cold["mcp_import_ms"] + cold["db_init_ms"]
    assert not any(cold["lazy_modules_loaded"].values()), (
        "heavy backends must stay lazy on MCP import"
    )
    assert Path(cold["source_root"]).resolve() == Path(__file__).resolve().parents[1]
    assert result["cold_existing_db"][0]["schema_version"] == cold["schema_version"]
    scale = result["scales"]["40"]
    assert scale["vector_backend"] == "chromadb-hnsw"
    assert scale["fts_rows"] == 40
    assert scale["fts_rebuild"]["iterations"] == 5
    assert len(scale["index_digest"]) == 64
    for scenario in scale["scenarios"].values():
        assert scenario["iterations"] == 2
        assert scenario["p50_ms"] > 0
        assert scenario["p95_ms"] >= scenario["p50_ms"]
        assert all(scenario["hit_ids"])


@pytest.mark.parametrize("option", ["--scale", "--iterations", "--repeats", "--workers"])
def test_stage6_benchmark_rejects_empty_measurements(option):
    with pytest.raises(SystemExit) as exc:
        load_benchmark().main([option, "0"])
    assert exc.value.code == 2
