"""
scripts/migrate_v04_lifecycle.py
幂等迁移：为 memoryitem 表添加 v0.4 Knowledge Lifecycle 字段。
"""
import sqlite3, pathlib, sys

DB_PATH = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("lantai.db")

COLUMNS = [
    ("lifecycle_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("superseded_by",    "TEXT"),
    ("weakened_at",      "DATETIME"),
    ("superseded_at",    "DATETIME"),
    ("retired_at",       "DATETIME"),
]

def migrate(db_path: pathlib.Path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(memoryitem)")}
    added = []
    for col, typedef in COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE memoryitem ADD COLUMN {col} {typedef}")
            added.append(col)
    # 建索引（幂等：IF NOT EXISTS）
    cur.execute("CREATE INDEX IF NOT EXISTS ix_memoryitem_lifecycle_status ON memoryitem(lifecycle_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_memoryitem_superseded_by ON memoryitem(superseded_by)")
    con.commit()
    con.close()
    if added:
        print(f"Added columns: {added}")
    else:
        print("Already up-to-date.")

if __name__ == "__main__":
    migrate(DB_PATH)
