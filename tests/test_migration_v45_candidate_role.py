"""v45：已知角色值恢复为 ORM 枚举成员名，不改正文、时间或未知值。"""

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.models.tables import CognitiveRole, MemoryCandidate, MemoryItem
from lantai.storage.migrations import (
    TARGET_SCHEMA_VERSION,
    _migrate_v19,
    _migrate_v33,
    apply_migrations,
    get_migration_failures,
)


def _legacy_candidates(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    instant = datetime(2026, 8, 2, 14, 41, 19, 234999, tzinfo=UTC)
    with Session(engine) as session:
        for role in CognitiveRole:
            session.add(
                MemoryCandidate(
                    id=f"legacy-{role.value}",
                    document_id="doc-role",
                    role=role,
                    summary=f"保留候选正文：{role.value}",
                    provenance={"prompt": "extract-v1", "actor": "legacy-import"},
                    created_at=instant,
                    review_due_at=instant,
                )
            )
        session.add(MemoryCandidate(id="canonical", document_id="doc-role"))
        session.add(MemoryItem(id="unrelated", content="表外正文不得变更"))
        session.commit()
    with sqlite3.connect(path) as conn:
        for role in CognitiveRole:
            conn.execute(
                "UPDATE memorycandidate SET role=? WHERE id=?",
                (role.value, f"legacy-{role.value}"),
            )
        conn.execute("UPDATE memoryitem SET role='observation' WHERE id='unrelated'")
        conn.execute("PRAGMA user_version=44")
    return engine


def _rows(conn):
    conn.row_factory = sqlite3.Row
    return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM memorycandidate")}


def test_v45_restores_known_roles_without_other_column_changes(tmp_path, caplog):
    path = tmp_path / "legacy-role.db"
    engine = _legacy_candidates(path)
    try:
        # 实证前置：历史合法值拼写在当前 ORM 下仍不可读。
        with Session(engine) as session:
            with pytest.raises(LookupError, match="observation"):
                session.get(MemoryCandidate, "legacy-observation")
        with sqlite3.connect(path) as conn, caplog.at_level("INFO", logger="lantai"):
            before = _rows(conn)
            other_before = conn.execute("SELECT * FROM memoryitem").fetchall()
            apply_migrations(conn)
            after = _rows(conn)
            assert TARGET_SCHEMA_VERSION >= 45
            assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
            assert get_migration_failures() == []
            assert before.keys() == after.keys()
            for row_id, row in before.items():
                expected = dict(row)
                if row_id.startswith("legacy-"):
                    expected["role"] = CognitiveRole(row["role"]).name
                assert after[row_id] == expected
            assert conn.execute("SELECT * FROM memoryitem").fetchall() == other_before
            assert "normalized=6" in caplog.text
            assert "unknown=0" in caplog.text
            assert "null=0" in caplog.text
            # 同一迁移链重复调用不得再动任何行。
            change_count = conn.total_changes
            apply_migrations(conn)
            assert conn.total_changes == change_count
        with Session(engine) as session:
            rows = session.exec(select(MemoryCandidate)).all()
            assert len(rows) == len(CognitiveRole) + 1
            for row in rows:
                assert isinstance(row.role, CognitiveRole)
                assert row.created_at.tzinfo is UTC
    finally:
        engine.dispose()


def test_v45_preserves_unknown_null_and_nonexact_values_with_warning(caplog):
    from lantai.storage.migrations import _migrate_v45

    # NOCASE 列也必须精确匹配小写值；不得把 Observation 当作已证实来源。
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, role TEXT COLLATE NOCASE)")
        inputs = ["observation", "OBSERVATION", "Observation", " observation ", "unknown", "", None]
        conn.executemany("INSERT INTO memorycandidate VALUES (?, ?)", enumerate(inputs))
        with caplog.at_level("INFO", logger="lantai"):
            _migrate_v45(conn)
        rows = conn.execute(
            "SELECT role FROM memorycandidate ORDER BY CAST(id AS INTEGER)"
        ).fetchall()
        assert [row[0] for row in rows] == ["OBSERVATION", *inputs[1:]]
        assert "normalized=1" in caplog.text
        assert "unknown=4" in caplog.text
        assert "null=1" in caplog.text
        assert any(
            record.levelname == "WARNING" and "保留" in record.message for record in caplog.records
        )
        before = conn.total_changes
        _migrate_v45(conn)
        assert conn.total_changes == before


@pytest.mark.parametrize("shape", ["no-table", "no-role"])
def test_v45_absent_legacy_table_or_column_is_safe(shape):
    from lantai.storage.migrations import _migrate_v45

    with sqlite3.connect(":memory:") as conn:
        if shape == "no-role":
            conn.execute("CREATE TABLE memorycandidate (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO memorycandidate VALUES ('kept')")
        _migrate_v45(conn)
        if shape == "no-role":
            assert conn.execute("SELECT id FROM memorycandidate").fetchall() == [("kept",)]


@pytest.mark.parametrize(
    "migration, table_name",
    [(_migrate_v19, "memorycandidate"), (_migrate_v19, "memoryitem"), (_migrate_v33, "memoryitem")],
)
def test_legacy_role_column_creation_defaults_to_enum_member_name(migration, table_name):
    with sqlite3.connect(":memory:") as conn:
        conn.execute(f"CREATE TABLE {table_name} (id TEXT PRIMARY KEY)")
        conn.execute(f"INSERT INTO {table_name} VALUES ('before')")
        migration(conn)
        conn.execute(f"INSERT INTO {table_name} (id) VALUES ('after')")
        assert conn.execute(f"SELECT role FROM {table_name} ORDER BY id").fetchall() == [
            ("OBSERVATION",),
            ("OBSERVATION",),
        ]
