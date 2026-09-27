"""Ticket 01: Schema 版本化迁移（PRAGMA user_version + apply_migrations）

不 mock 冒烟测试：真实临时 SQLite 库直调 apply_migrations，验证
- 全新库（列已齐全）→ user_version == CURRENT_SCHEMA_VERSION，幂等
- v1 老库（缺三列）→ 三列补齐 + 默认值正确 + 数据零丢失
- 已迁移库重复启动 → 幂等，user_version 仍为 CURRENT_SCHEMA_VERSION
- 异常路径（表不存在）不阻断启动
"""

import sqlite3

import pytest

from lantai.storage.db import CURRENT_SCHEMA_VERSION, apply_migrations


def _columns(conn, table: str) -> set:
    # table-valued PRAGMA 走参数绑定（防注入纪律，与 db.py 一致）
    return {
        r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
    }


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
        before = {
            t: _columns(conn, t) for t in ("memoryitem", "retrieval_event", "memorycandidate")
        }
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
        assert (
            conn.execute("SELECT decay_class FROM memoryitem WHERE id='m1'").fetchone()[0]
            == "episodic"
        )
        assert (
            conn.execute("SELECT is_system_noise FROM retrieval_event WHERE id='r1'").fetchone()[0]
            == 0
        )
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

    def test_v22_to_v23_creates_consolidation_run(self, tmp_path):
        """v22 老库 → v23（ADR-0050/票 07）：consolidation_run 运行留痕表补建，
        字段齐全、幂等、存量数据保持。"""
        path = tmp_path / "v22.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE retrieval_event (
                id TEXT PRIMARY KEY, query TEXT, request_id TEXT,
                receipt_status TEXT DEFAULT 'pending', receipt_at DATETIME
            );
            """
        )
        conn.execute("INSERT INTO retrieval_event (id, query) VALUES (?, ?)", ("r1", "老查询"))
        conn.execute("PRAGMA user_version = 22")
        conn.commit()

        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        cols = _columns(conn, "consolidation_run")
        for col in (
            "id",
            "ran_at",
            "mode",
            "clusters",
            "purified_ok",
            "proposals_created",
            "skipped_dupes",
            "skipped_rejected_cooldown",
            "skipped_lowq",
            "pruned",
            "error",
        ):
            assert col in cols
        assert conn.execute("SELECT id FROM retrieval_event WHERE id = ?", ("r1",)).fetchall() == [
            ("r1",)
        ]
        # 幂等：重复启动不重建不报错
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        conn.close()

    def test_v23_to_v24_adds_decided_at(self, tmp_path):
        """v23 老库 → v24（ADR-0053）：memoryproposal 补 decided_at 列，老行不回填
        （NULL 即「未记录」的事实状态），存量数据与既有列保持。"""
        path = tmp_path / "v23.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE memoryproposal (
                id TEXT PRIMARY KEY,
                proposal_type TEXT,
                evidence_ids TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME,
                applied_at DATETIME
            );
            """
        )
        conn.execute(
            "INSERT INTO memoryproposal (id, proposal_type, status, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("p_old", "consolidation", "rejected", "2026-01-01 00:00:00"),
        )
        conn.execute("PRAGMA user_version = 23")
        conn.commit()

        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert "decided_at" in _columns(conn, "memoryproposal")
        # 老行不回填：decided_at IS NULL 是事实状态（冷却读侧回退 created_at）
        assert (
            conn.execute(
                "SELECT decided_at FROM memoryproposal WHERE id = ?", ("p_old",)
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT created_at FROM memoryproposal WHERE id = ?", ("p_old",)
            ).fetchone()[0]
            == "2026-01-01 00:00:00"
        )

        # 幂等：重复启动不加列不报错（_has_column 守卫）
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        conn.close()

    def test_v24_new_db_skips_column_add(self, tmp_path):
        """v24 新库（create_all 已含 decided_at）→ 不加列、user_version 定格。"""
        path = tmp_path / "v24.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE memoryproposal (
                id TEXT PRIMARY KEY,
                proposal_type TEXT,
                evidence_ids TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME,
                applied_at DATETIME,
                decided_at DATETIME
            );
            """
        )
        conn.execute(
            "INSERT INTO memoryproposal (id, proposal_type, status, decided_at)"
            " VALUES (?, ?, ?, ?)",
            ("p_new", "consolidation", "rejected", "2026-06-01 12:00:00"),
        )
        conn.execute("PRAGMA user_version = 24")
        conn.commit()

        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        # 已落值不被迁移覆盖
        assert (
            conn.execute(
                "SELECT decided_at FROM memoryproposal WHERE id = ?", ("p_new",)
            ).fetchone()[0]
            == "2026-06-01 12:00:00"
        )
        conn.close()


class TestIndexCreationFailureIsLogged:
    """迁移期索引创建失败必须留痕（不得静默 pass）。

    背景：db.py 曾有 4 处 `except Exception: pass` 吞掉 CREATE INDEX 失败，
    索引静默缺失 → 查询退化且无任何信号。同文件其余 40+ 处 except 都有
    logger.warning（如 :40 列检查跳过），这 4 处是疏漏而非设计。
    修法：只加留痕、不改行为（索引失败仍不阻断迁移，老库必须能起来）。
    """

    @staticmethod
    def _db_with_index_name_taken(tmp_path):
        """构造「索引名被占」的老库：memoryitem 存在，且库中已有一个
        与 `idx_memoryitem_domain` 同名的**表**。

        SQLite 的索引名与表名共享同一命名空间，所以 v17 的
        `CREATE INDEX IF NOT EXISTS idx_memoryitem_domain` 必然抛
        `OperationalError: there is already a table named ...`——
        这正是索引创建失败的**真实失败形态**（名字被占，不是列缺失：
        列缺失时 v17 会先 ALTER 补列，索引反而建得起来）。

        现实中也存在：历史脚本/人工建过同名表，或另一条迁移链先建了表。
        """
        conn = sqlite3.connect(str(tmp_path / "index_name_taken.db"))
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
            CREATE TABLE idx_memoryitem_domain (x);
            """
        )
        conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m1', '老数据')")
        conn.commit()
        return conn

    def test_index_failure_logs_warning_and_migration_continues(self, tmp_path, caplog):
        """索引建不上 → warning 留痕 + 迁移照常完成 + 存量数据不丢。

        不 mock：真实 SQLite 库 + 真实 CREATE INDEX 失败路径。
        失败形态选「索引名被表占用」而非「缺列」——缺列时迁移会先补列，
        索引建得起来，warning 反而不该出现（见 test_no_spurious_warning）。
        """
        import logging

        from lantai.core.logger import logger

        conn = self._db_with_index_name_taken(tmp_path)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            apply_migrations(conn)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]

        # ① 留痕：至少有 warning（不是静默 pass）
        assert warnings, "索引创建失败必须留痕，不得静默吞掉"
        joined = " | ".join(str(r.getMessage()) for r in warnings)
        assert "索引" in joined or "index" in joined.lower(), (
            f"warning 须点明是索引创建失败，实得: {joined}"
        )

        # ② 行为不变：迁移仍完成（老库必须能起来）
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION

        # ③ 存量数据零丢失
        assert conn.execute("SELECT id, content FROM memoryitem").fetchall() == [("m1", "老数据")]
        conn.close()

    def test_missing_column_db_creates_index_without_warning(self, tmp_path, caplog):
        """反例护栏：老库缺 domain 列时**不该**有 warning。

        v17 先 ALTER 补列再建索引（db.py:262-263），所以「缺列」形态下索引
        建得起来。留痕只针对**真失败**；若这条也报 warning，说明留痕变成噪音，
        该修的是「宁滥勿缺」的反模式，不是把噪音当真信号。
        """
        import logging

        from lantai.core.logger import logger

        conn = sqlite3.connect(str(tmp_path / "no_domain.db"))
        conn.executescript(
            """
            CREATE TABLE memoryitem (
                id TEXT PRIMARY KEY,
                content TEXT,
                lane TEXT DEFAULT 'general',
                status TEXT DEFAULT 'active',
                decay_score REAL DEFAULT 1.0
            );
            CREATE TABLE retrieval_event (id TEXT PRIMARY KEY, query TEXT);
            CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, summary TEXT, status TEXT DEFAULT 'new');
            """
        )
        conn.commit()

        with caplog.at_level(logging.WARNING, logger=logger.name):
            apply_migrations(conn)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, (
            f"缺列库（v17 会先补列）不应有 warning，实得: {[r.getMessage() for r in warnings]}"
        )
        # 补列 + 索引都建起来了
        assert "domain" in _columns(conn, "memoryitem")
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_memoryitem_domain" in names
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        conn.close()

    def test_no_spurious_warning_on_healthy_db(self, tmp_path, caplog):
        """健康库（列齐全）不得误报 warning——留痕不能变成噪音。"""
        import logging

        from lantai.core.logger import logger

        path = tmp_path / "healthy.db"
        conn = _make_legacy_db(path, with_new_columns=True)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            apply_migrations(conn)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f"健康库不应有 warning，实得: {[r.getMessage() for r in warnings]}"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        conn.close()

    # 四个留痕站点逐一覆盖：索引名被同名表占用 → CREATE INDEX 必失败。
    # 只测 v17 会漏掉另外三处（票 01 修了 4 处，就得有 4 处活着的留痕）。
    @pytest.mark.parametrize(
        ("colliding_table", "extra_ddl", "expected_marker"),
        [
            pytest.param(
                "idx_memoryitem_domain",
                "",
                "idx_memoryitem_domain",
                id="v17-domain",
            ),
            pytest.param(
                "ix_memoryitem_lifecycle_status",
                "",
                "ix_memoryitem_lifecycle_status",
                id="v20-lifecycle",
            ),
            pytest.param(
                "ix_memoryitem_event_time",
                # v21 的索引创建被 `valid_from`/`created_at` 双列守卫包着，
                # 老库必须自带这两列才会走到建索引这一步。
                "ALTER TABLE memoryitem ADD COLUMN created_at DATETIME;"
                "ALTER TABLE memoryitem ADD COLUMN valid_from DATETIME;"
                "ALTER TABLE memoryitem ADD COLUMN valid_to DATETIME;",
                "event_time",
                id="v21-genglou",
            ),
            pytest.param(
                "ix_retrieval_event_request_id",
                "",
                "request_id",
                id="v22-receipt-chain",
            ),
        ],
    )
    def test_each_index_site_logs_on_failure(
        self, tmp_path, caplog, colliding_table, extra_ddl, expected_marker
    ):
        """四处索引留痕**各自**都是活的：改回 pass 就必须有测试红。"""
        import logging

        from lantai.core.logger import logger

        conn = sqlite3.connect(str(tmp_path / f"collide_{colliding_table}.db"))
        conn.executescript(
            """
            CREATE TABLE memoryitem (
                id TEXT PRIMARY KEY,
                content TEXT,
                lane TEXT DEFAULT 'general',
                status TEXT DEFAULT 'active',
                decay_score REAL DEFAULT 1.0
            );
            CREATE TABLE retrieval_event (id TEXT PRIMARY KEY, query TEXT);
            CREATE TABLE memorycandidate (id TEXT PRIMARY KEY, summary TEXT, status TEXT DEFAULT 'new');
            """
            + extra_ddl
        )
        conn.execute(f"CREATE TABLE {colliding_table} (x)")
        conn.execute("INSERT INTO memoryitem (id, content) VALUES ('m1', '老数据')")
        conn.commit()

        with caplog.at_level(logging.WARNING, logger=logger.name):
            apply_migrations(conn)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, f"{colliding_table} 建索引失败必须留痕，不得静默吞掉"
        joined = " | ".join(str(r.getMessage()) for r in warnings)
        assert expected_marker in joined, f"warning 须点明 {expected_marker}，实得: {joined}"

        # 留痕不改变行为：迁移照常完成，存量数据零丢失
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT id, content FROM memoryitem").fetchall() == [("m1", "老数据")]
        conn.close()
