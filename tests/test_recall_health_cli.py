"""召回诊断：合成 SQLite 的真实 SQL 冒烟及只读错误路径。"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts import recall_health


def _create_events(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE retrieval_event "
            "(created_at TEXT, zero_result INTEGER, is_system_noise INTEGER)"
        )
        conn.executemany(
            "INSERT INTO retrieval_event VALUES (?, ?, ?)",
            [
                (datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "), 1, 0),
                (datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "), 0, 1),
            ],
        )


def test_collect_recall_health_real_sqlite_and_render(tmp_path):
    path = tmp_path / "events.db"
    _create_events(path)
    with sqlite3.connect(path) as conn:
        result = recall_health.collect_recall_health(conn, days=1)
    assert result["total"] == 2
    assert result["zero_results"] == 1
    assert result["system_noise"] == 1
    assert result["daily"] == [(datetime.now(UTC).date().isoformat(), 2, 1)]
    rendered = recall_health.render_health(result)
    assert "50.0%" in rendered
    assert "2 次检索" in rendered


def test_collect_empty_event_table(tmp_path):
    path = tmp_path / "empty-events.db"
    _create_events(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM retrieval_event")
        result = recall_health.collect_recall_health(conn)
    assert result == {
        "window_days": 14,
        "total": 0,
        "zero_results": 0,
        "system_noise": 0,
        "last_retrieval": "无记录",
        "daily": [],
    }
    assert "N/A" in recall_health.render_health(result)


def test_collect_window_matches_real_orm_utc_storage(tmp_path, monkeypatch):
    from sqlmodel import Session, create_engine

    from lantai.models.tables import RetrievalEvent

    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # 模拟 UTC+8 主机；显式 UTC 调用才得到库里的同一时刻。
            return (
                now.astimezone(tz)
                if tz is not None
                else (now + timedelta(hours=8)).replace(tzinfo=None)
            )

    path = tmp_path / "orm-events.db"
    engine = create_engine(f"sqlite:///{path}")
    RetrievalEvent.__table__.create(engine)
    cutoff = now - timedelta(days=1)
    try:
        with Session(engine) as session:
            for index, created in enumerate(
                [
                    cutoff - timedelta(microseconds=1),
                    cutoff,
                    cutoff + timedelta(microseconds=1),
                    now,
                ]
            ):
                session.add(
                    RetrievalEvent(
                        id=f"diagnostic-{index}",
                        trace_id="diagnostic",
                        query_norm_hash="synthetic",
                        param_snapshot_hash="synthetic",
                        created_at=created,
                        zero_result=index == 1,
                    )
                )
            session.commit()
        monkeypatch.setattr(recall_health, "datetime", Clock)
        with sqlite3.connect(path) as conn:
            result = recall_health.collect_recall_health(conn, days=1)
        assert result["total"] == 3
        assert result["zero_results"] == 1
        assert sum(row[1] for row in result["daily"]) == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize("days", [0, -1, True, 1.5, 10**12])
def test_collect_rejects_invalid_window(days):
    with sqlite3.connect(":memory:") as conn, pytest.raises(ValueError, match="days"):
        recall_health.collect_recall_health(conn, days=days)


@pytest.mark.parametrize("days", ["0", "-1", "1000000000000"])
def test_cli_invalid_window_is_usage_error(days, capsys):
    with pytest.raises(SystemExit) as exc:
        recall_health.main(["--days", days])
    assert exc.value.code == 2
    assert "--days" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["missing", "corrupt", "empty-schema"])
def test_cli_database_error_is_readable_and_never_creates_database(
    kind, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "diagnostic.db"
    if kind == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif kind == "empty-schema":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
    before = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(recall_health.settings, "DATABASE_URL", f"sqlite:///{path}")
    assert recall_health.main([]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "recall-health:" in captured.err
    assert "Traceback" not in captured.err
    assert str(path) in captured.err
    assert (path.read_bytes() if path.exists() else None) == before


def test_cli_non_sqlite_is_readable(monkeypatch, capsys):
    monkeypatch.setattr(recall_health.settings, "DATABASE_URL", "postgresql://example/diagnostic")
    assert recall_health.main([]) == 1
    assert "SQLite" in capsys.readouterr().err


def test_cli_success_real_database_is_unchanged(tmp_path, monkeypatch, capsys):
    path = tmp_path / "events #&.db"
    _create_events(path)
    before = path.read_bytes()
    monkeypatch.setattr(recall_health.settings, "DATABASE_URL", f"sqlite:///{path}")
    assert recall_health.main(["--days", "1"]) == 0
    assert "50.0%" in capsys.readouterr().out
    assert path.read_bytes() == before
