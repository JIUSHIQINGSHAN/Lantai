"""原文先存首期：真实旧库升级、新库结构与默认关闭，不读宿主数据。"""

import sqlite3
from contextlib import closing

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from lantai.core.settings import Settings
from lantai.storage.migrations import (
    TARGET_SCHEMA_VERSION,
    apply_migrations,
    get_migration_failures,
)


def test_legacy_database_adds_message_and_job_without_rewriting_existing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE old_marker (id TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO old_marker VALUES ('one', 'original')")
        conn.execute("PRAGMA user_version = 46")
        conn.commit()
        apply_migrations(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"conversation_message", "conversation_job"} <= names
        assert conn.execute("SELECT value FROM old_marker WHERE id='one'").fetchone() == (
            "original",
        )
        assert conn.execute("SELECT COUNT(*) FROM conversation_message").fetchone() == (0,)
        assert conn.execute("PRAGMA user_version").fetchone() == (TARGET_SCHEMA_VERSION,)
        assert get_migration_failures() == []
        apply_migrations(conn)
        assert get_migration_failures() == []


def test_conversation_capture_default_is_closed():
    assert Settings.model_fields["FEATURE_CONVERSATION_CAPTURE"].default is False


def test_new_and_upgraded_conversation_schema_match(tmp_path):
    new_path = tmp_path / "new.db"
    old_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{new_path.as_posix()}")
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    with closing(sqlite3.connect(old_path)) as conn:
        conn.execute("PRAGMA user_version=46")
        apply_migrations(conn)
        assert get_migration_failures() == []
    with (
        closing(sqlite3.connect(new_path)) as fresh,
        closing(sqlite3.connect(old_path)) as upgraded,
    ):
        for table in ("conversation_message", "conversation_job"):
            new_columns = fresh.execute(f"PRAGMA table_info({table})").fetchall()
            assert new_columns, table
            assert new_columns == upgraded.execute(f"PRAGMA table_info({table})").fetchall()
            assert (
                fresh.execute(f"PRAGMA index_list({table})").fetchall()
                == upgraded.execute(f"PRAGMA index_list({table})").fetchall()
            )
