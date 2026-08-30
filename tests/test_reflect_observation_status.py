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
    """察窗（ADR-0027）：窗口内计数，不限连续。"""
    path, conn = _database(tmp_path)
    start = datetime.now().replace(minute=1, second=0, microsecond=0) - timedelta(days=6)
    for offset in range(7):
        _insert(conn, start + timedelta(days=offset))
    _insert(conn, start + timedelta(days=6, hours=-6), source="manual")
    _insert(conn, start - timedelta(days=1), source="unknown")
    conn.commit()
    conn.close()

    from scripts.reflect_observation_status import collect_observation_status
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db, required_runs=7, window_days=14)

    assert status["qualified_days"] == 7
    assert status["ready"] is True
    assert status["source_counts"] == {"scheduled": 7, "manual": 1, "unknown": 1}


def test_collect_observation_status_breaks_on_failure_and_main_check_blocks(tmp_path):
    """察窗（ADR-0027）：窗口内计数，curate_failed 日不算合格，但合格日照计。"""
    path, conn = _database(tmp_path)
    start = datetime.now().replace(minute=1, second=0, microsecond=0) - timedelta(days=6)
    for offset in range(5):
        _insert(conn, start + timedelta(days=offset))
    _insert(conn, start + timedelta(days=5), curate_failed=1)  # 不合格日
    _insert(conn, start + timedelta(days=6))  # 合格日
    conn.commit()
    conn.close()

    from scripts.reflect_observation_status import collect_observation_status, main
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db, required_runs=7, window_days=14)

    # 5 天合格 + 1 天失败 + 1 天合格 = 6 天合格（失败日不算）
    assert status["qualified_days"] == 6
    assert status["ready"] is False
    assert main(["--check", "--required", "7", "--window", "14"], database_path=path) == 1


def test_collect_observation_status_respects_reference_date(tmp_path):
    """验证 reference_date 锚定历史日期下的窗口统计确定性。"""
    path, conn = _database(tmp_path)
    hist_start = datetime(2026, 8, 16, 22, 1)
    for offset in range(7):
        _insert(conn, hist_start + timedelta(days=offset))
    conn.commit()
    conn.close()

    from scripts.reflect_observation_status import collect_observation_status
    with sqlite3.connect(path) as db:
        # 以最后一天 2026-08-22 作为 reference_date
        status = collect_observation_status(
            db, required_runs=7, window_days=14,
            reference_date=hist_start.date() + timedelta(days=6),
        )

    assert status["qualified_days"] == 7
    assert status["ready"] is True


def test_collect_observation_status_reports_pre_migration_database(tmp_path):
    path = tmp_path / "pre-v13.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE reflect_run (run_at DATETIME)")

    from scripts.reflect_observation_status import collect_observation_status
    with sqlite3.connect(path) as db:
        status = collect_observation_status(db)

    assert status["migration_required"] is True
    assert status["ready"] is False
