"""
scripts/migrate_v03_promotion_trace.py
为 MemoryItem 表新增 promotion_trace 字段（v0.3 晋升追踪）。
幂等：字段已存在时静默跳过。
"""

import sqlite3


def apply_promotion_trace_migration() -> None:
    """为 memoryitem 补 promotion_trace 列（幂等；DDL 固定字面量，零拼接）。

    目标库固定为本仓工作目录的 lantai.db（一次性历史迁移脚本，无参数）。"""
    conn = sqlite3.connect("lantai.db")
    try:
        cursor = conn.cursor()

        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(memoryitem)")
        columns = {row[1] for row in cursor.fetchall()}

        if not columns:
            print("[migrate_v03] memoryitem table not found, skipping (fresh DB).")
        elif "promotion_trace" in columns:
            print("[migrate_v03] promotion_trace already exists, skipping.")
        else:
            cursor.execute("ALTER TABLE memoryitem ADD COLUMN promotion_trace TEXT DEFAULT '{}'")
            conn.commit()
            print("[migrate_v03] ✅ Added promotion_trace column to memoryitem.")
    finally:
        conn.close()


if __name__ == "__main__":
    apply_promotion_trace_migration()
