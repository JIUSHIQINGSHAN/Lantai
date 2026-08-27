from datetime import datetime, timedelta
import sqlite3


def _database(tmp_path):
    path = tmp_path / "observation.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE reflect_run (run_at DATETIME, source TEXT, "
        "curate_failed INTEGER, rejecter_failed INTEGER, error TEXT)")
    return path, conn


def _insert(conn, when, source="scheduled", curate_failed=0, rejecter_failed=0,
            error=""):
    conn.execute(
        "INSERT INTO reflect_run VALUES (?, ?, ?, ?, ?)",
        (when.isoformat(sep=" "), source, curate_failed, rejecter_failed, error))


def test_collect_observation_status_counts_only_consecutive_scheduled_successes(tmp_path):
    path, conn = _database(tmp_path)
    start = datetime(2026, 8, 16, 22, 1)
    for offset in range(7):
        _insert(conn, start + timedelta(days=offset))
    _insert(conn, start + timedelta(days=6, hours=-6), source="manual")
    _insert(conn, start - timedelta(days=1), source="unknown")
    conn.commit()
    conn.close()

    from scripts.reflect_observation_status import collect_observation_status
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db)

    assert status["consecutive_successes"] == 7
    assert status["ready"] is True
    assert status["source_counts"] == {"scheduled": 7, "manual": 1, "unknown": 1}


def test_collect_observation_status_breaks_on_failure_and_main_check_blocks(tmp_path):
    path, conn = _database(tmp_path)
    start = datetime(2026, 8, 16, 22, 1)
    for offset in range(5):
        _insert(conn, start + timedelta(days=offset))
    _insert(conn, start + timedelta(days=5), curate_failed=1)
    _insert(conn, start + timedelta(days=6))
    conn.commit()
    conn.close()

    from scripts.reflect_observation_status import collect_observation_status, main
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db)

    assert status["consecutive_successes"] == 1
    assert status["ready"] is False
    assert main(["--check"], database_path=path) == 1


def test_collect_observation_status_reports_pre_migration_database(tmp_path):
    path = tmp_path / "pre-v13.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE reflect_run (run_at DATETIME)")

    from scripts.reflect_observation_status import collect_observation_status
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db)

    assert status["migration_required"] is True
    assert status["ready"] is False
