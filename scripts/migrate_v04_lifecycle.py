"""
scripts/migrate_v04_lifecycle.py
幂等迁移：为 memoryitem 表添加 v0.4 Knowledge Lifecycle 字段。
"""

import pathlib
import sqlite3

def apply_lifecycle_migration(db_path: pathlib.Path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(memoryitem)")}
    if not existing:
        print("memoryitem table not found, skipping (fresh DB).")
        con.close()
        return
    added = []
    # DDL 固定字面量逐条内联（SQLite ALTER TABLE 不支持绑定参数，防注入纪律）
    if "lifecycle_status" not in existing:
        cur.execute(
            "ALTER TABLE memoryitem ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'"
        )
        added.append("lifecycle_status")
    if "superseded_by" not in existing:
        cur.execute("ALTER TABLE memoryitem ADD COLUMN superseded_by TEXT")
        added.append("superseded_by")
    if "weakened_at" not in existing:
        cur.execute("ALTER TABLE memoryitem ADD COLUMN weakened_at DATETIME")
        added.append("weakened_at")
    if "superseded_at" not in existing:
        cur.execute("ALTER TABLE memoryitem ADD COLUMN superseded_at DATETIME")
        added.append("superseded_at")
    if "retired_at" not in existing:
        cur.execute("ALTER TABLE memoryitem ADD COLUMN retired_at DATETIME")
        added.append("retired_at")
    # 建索引（幂等：IF NOT EXISTS）
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_memoryitem_lifecycle_status ON memoryitem(lifecycle_status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_memoryitem_superseded_by ON memoryitem(superseded_by)"
    )
    con.commit()
    con.close()
    if added:
        print(f"Added columns: {added}")
    else:
        print("Already up-to-date.")


if __name__ == "__main__":
    # 一次性历史迁移脚本：目标库固定为本仓工作目录的 lantai.db（无参数）
    apply_lifecycle_migration(pathlib.Path("lantai.db"))
