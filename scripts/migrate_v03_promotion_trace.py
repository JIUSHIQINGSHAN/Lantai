"""
scripts/migrate_v03_promotion_trace.py
为 MemoryItem 表新增 promotion_trace 字段（v0.3 晋升追踪）。
幂等：字段已存在时静默跳过。
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("LANTAI_DB", "lantai.db")


def migrate(db_path: str = DB_PATH) -> None:
    print(f"[migrate_v03] target DB: {db_path}")
    if not os.path.exists(db_path):
        print(
            f"[migrate_v03] DB not found at {db_path}, skipping (will be created fresh on first run)"
        )
        return

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(memoryitem)")
        columns = {row[1] for row in cursor.fetchall()}

        if "promotion_trace" in columns:
            print("[migrate_v03] promotion_trace already exists, skipping.")
        else:
            cursor.execute("ALTER TABLE memoryitem ADD COLUMN promotion_trace TEXT DEFAULT '{}'")
            conn.commit()
            print("[migrate_v03] ✅ Added promotion_trace column to memoryitem.")
    finally:
        conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    migrate(path)
