"""v28:老库 memoryedge 缺 reason 列时,迁移必须补上并能被 supersedes 查询使用。

不 mock:真实临时 SQLite 库 + 真实 apply_migrations。
"""

import sqlite3

from sqlmodel import Session, select

from lantai.models.tables import MemoryEdge
from lantai.storage.db import init_db
from lantai.storage.migrations import TARGET_SCHEMA_VERSION


def _fresh(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setattr("lantai.core.settings.settings.DATABASE_URL", url)
    import lantai.storage.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db_mod.create_engine(url))
    return db_path


def _columns(db_path, table):
    c = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def test_v28_adds_reason_to_legacy_memoryedge_and_edge_query_works(tmp_path, monkeypatch):
    db_path = _fresh(tmp_path, monkeypatch)
    init_db()
    c = sqlite3.connect(str(db_path))
    c.execute("ALTER TABLE memoryedge RENAME TO _me_tmp")
    cols = [r[1] for r in c.execute("PRAGMA table_info(_me_tmp)") if r[1] != "reason"]
    c.execute(
        "CREATE TABLE memoryedge AS SELECT " + ", ".join(f'"{x}"' for x in cols) + " FROM _me_tmp"
    )
    c.execute("DROP TABLE _me_tmp")
    c.execute("PRAGMA user_version = 27")
    c.commit()
    c.close()
    assert "reason" not in _columns(db_path, "memoryedge")

    import lantai.storage.db as db_mod
    from lantai.storage.migrations import apply_migrations

    raw = sqlite3.connect(str(db_path))
    apply_migrations(raw)
    raw.close()

    assert TARGET_SCHEMA_VERSION >= 28
    assert "reason" in _columns(db_path, "memoryedge")

    with Session(db_mod.engine) as s:
        rows = s.exec(
            select(MemoryEdge)
            .where(MemoryEdge.relation.in_(["supersedes", "contradicts"]))
            .where(MemoryEdge.source_memory_id.in_(["mem_none"]))
        ).all()
        assert isinstance(rows, list)
