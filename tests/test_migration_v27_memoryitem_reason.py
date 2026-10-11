"""v27:老库 memoryitem 缺 reason 列时,迁移必须补上并能被列表查询使用。

不 mock:真实临时 SQLite 库 + 真实 apply_migrations。
"""

import sqlite3

from sqlmodel import Session, select

from lantai.models.tables import MemoryItem
from lantai.storage.db import TARGET_SCHEMA_VERSION, init_db


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


def test_v27_adds_reason_to_legacy_memoryitem_and_list_query_works(tmp_path, monkeypatch):
    db_path = _fresh(tmp_path, monkeypatch)
    init_db()
    c = sqlite3.connect(str(db_path))
    c.execute("ALTER TABLE memoryitem RENAME TO _mi_tmp")
    cols = [r[1] for r in c.execute("PRAGMA table_info(_mi_tmp)") if r[1] != "reason"]
    c.execute(
        "CREATE TABLE memoryitem AS SELECT " + ", ".join(f'"{x}"' for x in cols) + " FROM _mi_tmp"
    )
    c.execute("DROP TABLE _mi_tmp")
    c.execute("PRAGMA user_version = 26")
    c.commit()
    c.close()
    assert "reason" not in _columns(db_path, "memoryitem")

    import lantai.storage.db as db_mod
    from lantai.storage.migrations import apply_migrations

    raw = sqlite3.connect(str(db_path))
    apply_migrations(raw)
    raw.close()

    assert TARGET_SCHEMA_VERSION >= 27
    assert "reason" in _columns(db_path, "memoryitem")

    with Session(db_mod.engine) as s:
        rows = s.exec(select(MemoryItem).limit(3)).all()
        assert isinstance(rows, list)
