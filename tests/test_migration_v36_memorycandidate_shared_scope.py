"""v36: 老库 memorycandidate 缺 shared_scope 列时，迁移链必须补上（D-13/R-4）。

票 `.scratch/v0.66-next-window-2026-11/issues/05-v0660-release-gate.md` 续9：
发布门禁的 schema 探针只验证 create_all 建出的全新库，因此看不见"老库已到
TARGET_SCHEMA_VERSION、但迁移链从未补过某列"这类漂移。宿主真实库实测
（只读 PRAGMA）：user_version=35 == TARGET_SCHEMA_VERSION，而
`memorycandidate.shared_scope` 整列缺失——v35 只给 memoryitem / skillcrystal
补了 shared_scope，漏了 memorycandidate。

与 test_migration_v29 同一约定：不 mock，真实临时 SQLite + 真实 apply_migrations。
"""

import sqlite3

from sqlmodel import Session, SQLModel, create_engine, select

from lantai.storage.migrations import (
    TARGET_SCHEMA_VERSION,
    apply_migrations,
    get_migration_failures,
)


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _make_legacy_db(path):
    """造一个"已迁到 v35 的老库"：结构齐平但 memorycandidate 没有 shared_scope。

    与真实老库同形：列不存在、索引也不存在（SQLite 不允许对被索引的列做
    DROP COLUMN，所以先删索引）。
    """
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(path))
    conn.execute("DROP INDEX IF EXISTS ix_memorycandidate_shared_scope")
    conn.execute("ALTER TABLE memorycandidate DROP COLUMN shared_scope")
    conn.execute("PRAGMA user_version = 35")
    conn.commit()
    conn.close()


def test_legacy_db_missing_memorycandidate_shared_scope_is_restored(tmp_path):
    db_path = tmp_path / "old.db"
    _make_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # 前置：复现漂移——版本已是目标值，列却不存在
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 35
        assert "shared_scope" not in _columns(conn, "memorycandidate")

        apply_migrations(conn)
        conn.commit()

        assert TARGET_SCHEMA_VERSION >= 36, (
            f"迁移链目标版本仍是 {TARGET_SCHEMA_VERSION}，没有为新列开台阶"
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        assert "shared_scope" in _columns(conn, "memorycandidate"), (
            "老库迁到目标版本后 memorycandidate.shared_scope 仍然缺失：同袍作用域列没迁移"
        )
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='ix_memorycandidate_shared_scope'"
        ).fetchall()
        assert idx, "ix_memorycandidate_shared_scope 索引未创建"
    finally:
        conn.close()


def test_upgraded_legacy_db_writes_shared_scope_via_orm(tmp_path):
    """迁移后的老库必须能真实写入/读回 shared_scope（不 mock，冒烟）。"""
    from lantai.models.tables import MemoryCandidate

    db_path = tmp_path / "old2.db"
    _make_legacy_db(db_path)
    conn = sqlite3.connect(str(db_path))
    apply_migrations(conn)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with Session(engine) as s:
        s.add(MemoryCandidate(id="cand_1", document_id="doc_1", shared_scope="swarm_shared"))
        s.commit()
        got = s.exec(select(MemoryCandidate).where(MemoryCandidate.id == "cand_1")).first()
        assert got is not None
        assert got.shared_scope == "swarm_shared"
    engine.dispose()


def test_fresh_db_has_column_and_chain_is_idempotent(tmp_path):
    """全新库 create_all 天然有该列；重复跑迁移链必须幂等、无失败记录、可读写。"""
    from lantai.models.tables import MemoryCandidate

    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(db_path))
    try:
        assert "shared_scope" in _columns(conn, "memorycandidate")
        apply_migrations(conn)
        apply_migrations(conn)  # 列已存在时必须跳过，不得抛
        conn.commit()
        assert get_migration_failures() == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
    finally:
        conn.close()

    with Session(create_engine(f"sqlite:///{db_path.as_posix()}")) as s:
        s.add(MemoryCandidate(id="c_fresh", document_id="d", shared_scope="private"))
        s.commit()
        assert s.get(MemoryCandidate, "c_fresh").shared_scope == "private"
