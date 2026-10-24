"""v29:老库 cognitivepattern 缺 dedup_key 列时,迁移必须补上且反思幂等去重可用。

不 mock:真实临时 SQLite 库 + 真实 apply_migrations(同 test_migration_v28 约定)。
"""

import sqlite3

from sqlmodel import Session, SQLModel, create_engine, select

from lantai.storage.migrations import TARGET_SCHEMA_VERSION, apply_migrations


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_legacy_db(path):
    """建一个 v28 老库:表结构齐全但 cognitivepattern 没有 dedup_key 列。"""
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(path))
    conn.execute("ALTER TABLE cognitivepattern RENAME TO _cp_tmp")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(_cp_tmp)") if r[1] != "dedup_key"]
    conn.execute("DROP TABLE _cp_tmp")
    conn.execute(
        "CREATE TABLE cognitivepattern ("
        + ", ".join(f'"{c}" TEXT' for c in cols)
        + ", PRIMARY KEY (id))"
    )
    conn.execute("PRAGMA user_version = 28")
    conn.commit()
    conn.close()


def test_v29_adds_dedup_key_to_legacy_cognitivepattern(tmp_path):
    db_path = tmp_path / "old.db"
    _make_legacy_db(db_path)

    raw = sqlite3.connect(str(db_path))
    assert "dedup_key" not in _columns(raw, "cognitivepattern")
    raw.close()

    conn = sqlite3.connect(str(db_path))
    apply_migrations(conn)
    conn.close()

    assert TARGET_SCHEMA_VERSION >= 29
    cols = None
    c = sqlite3.connect(str(db_path))
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        cols = _columns(c, "cognitivepattern")
        idx = c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_cognitivepattern_dedup_key'"
        ).fetchall()
    finally:
        c.close()
    assert "dedup_key" in cols
    assert idx, "dedup_key 索引未创建"

    # ORM 侧可写入并按 dedup_key 查回（幂等去重的落库前提）
    from lantai.models.tables import CognitivePattern

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with Session(engine) as s:
        s.add(
            CognitivePattern(id="pat_1", pattern_type="recurrence", description="d", dedup_key="k1")
        )
        s.commit()
        got = s.exec(select(CognitivePattern).where(CognitivePattern.dedup_key == "k1")).first()
        assert got is not None and got.id == "pat_1"
    engine.dispose()
