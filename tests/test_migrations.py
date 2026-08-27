"""Ticket 01: Schema 版本化迁移（PRAGMA user_version + apply_migrations）

不 mock 冒烟测试：真实临时 SQLite 库直调 apply_migrations，验证
- 全新库（列已齐全）→ user_version == CURRENT_SCHEMA_VERSION，幂等
- v1 老库（缺三列）→ 三列补齐 + 默认值正确 + 数据零丢失
- 已迁移库重复启动 → 幂等，user_version 仍为 CURRENT_SCHEMA_VERSION
- 异常路径（表不存在）不阻断启动
"""
import sqlite3

from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _make_legacy_db(path, with_new_columns: bool) -> sqlite3.Connection:
    """构造未版本化老库：核心三表 + 可选新列 + 存量数据。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE memoryitem (
            id TEXT PRIMARY KEY,
            content TEXT,
            lane TEXT DEFAULT 'general',
            status TEXT DEFAULT 'active',
            decay_score REAL DEFAULT 1.0
        );
        CREATE TABLE retrieval_event (
            id TEXT PRIMARY KEY,
            query TEXT
        );
        CREATE TABLE memorycandidate (
            id TEXT PRIMARY KEY,
            summary TEXT,
            status TEXT DEFAULT 'new'
        );
        """
    )
    if with_new_columns:
        conn.executescript(
            """
            ALTER TABLE memoryitem ADD COLUMN decay_class TEXT DEFAULT 'episodic';
            ALTER TABLE retrieval_event ADD COLUMN is_system_noise BOOLEAN DEFAULT 0;
            ALTER TABLE memorycandidate ADD COLUMN review_due_at DATETIME;
            """
        )
    conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m1', '老数据A')")
    conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m2', '老数据B')")
    conn.execute("INSERT INTO retrieval_event (id, query) VALUES ('r1', '老查询')")
    conn.execute("INSERT INTO memorycandidate (id, summary) VALUES ('c1', '老候选')")
    conn.commit()
    return conn


class TestApplyMigrations:
    def test_fresh_db_bare_reaches_current_version(self, tmp_path):
        """空库（无表）也能完成版本记账到 CURRENT_SCHEMA_VERSION。"""
        conn = sqlite3.connect(str(tmp_path / "bare.db"))
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        conn.close()

    def test_fresh_db_with_full_columns_idempotent(self, tmp_path):
        """全新库（create_all 已含全部列）→ user_version==CURRENT_SCHEMA_VERSION，列不重复添加。"""
        path = tmp_path / "fresh.db"
        conn = _make_legacy_db(path, with_new_columns=True)
        before = {t: _columns(conn, t) for t in
                  ("memoryitem", "retrieval_event", "memorycandidate")}
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        for table, cols in before.items():
            assert cols.issubset(_columns(conn, table))  # v2 列集保留（v3 起可新增列）
        conn.close()

    def test_legacy_db_without_columns_adds_them_and_keeps_data(self, tmp_path):
        """v1 老库缺三列 → 补齐 + 默认值正确 + 存量数据零丢失。"""
        path = tmp_path / "legacy.db"
        conn = _make_legacy_db(path, with_new_columns=False)
        apply_migrations(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        # 三列补齐
        assert "decay_class" in _columns(conn, "memoryitem")
        assert "is_system_noise" in _columns(conn, "retrieval_event")
        assert "review_due_at" in _columns(conn, "memorycandidate")
        assert "deferred_at" in _columns(conn, "memorycandidate")
        assert "previous_review_due_at" in _columns(conn, "memorycandidate")
        assert "defer_count" in _columns(conn, "memorycandidate")
        assert "defer_reason" in _columns(conn, "memorycandidate")
        # 存量数据零丢失
        rows = conn.execute("SELECT id, content FROM memoryitem ORDER BY id").fetchall()
        assert rows == [("m1", "老数据A"), ("m2", "老数据B")]
        assert conn.execute("SELECT id FROM retrieval_event").fetchall() == [("r1",)]
        assert conn.execute("SELECT id FROM memorycandidate").fetchall() == [("c1",)]
        # 新列默认值生效
        assert conn.execute("SELECT decay_class FROM memoryitem WHERE id='m1'").fetchone()[0] == "episodic"
        assert conn.execute("SELECT is_system_noise FROM retrieval_event WHERE id='r1'").fetchone()[0] == 0
        conn.close()

    def test_repeated_run_is_noop(self, tmp_path):
        """已迁移库再次启动 → user_version 仍为 CURRENT_SCHEMA_VERSION，数据完好。"""
        path = tmp_path / "repeat.db"
        conn = _make_legacy_db(path, with_new_columns=False)
        apply_migrations(conn)
        apply_migrations(conn)  # 第二次启动
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM memoryitem").fetchone()[0] == 2
        conn.close()

    def test_pre_versioned_db_skips_rebasing(self, tmp_path):
        """已到当前版本的库：不动迁移链，数据保持。"""
        path = tmp_path / "v2.db"
        conn = _make_legacy_db(path, with_new_columns=True)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM memorycandidate").fetchone()[0] == 1
        conn.close()
