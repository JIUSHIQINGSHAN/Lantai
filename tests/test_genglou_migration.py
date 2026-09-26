"""更漏（ADR-0048 / roadmap-v2-execution 票 05-A）迁移 v20→v21 冒烟测试。

纪律：不 mock——对真实 SQLite 文件库（tmp_path）执行升列、回填、索引与重放；
断言 U5 回填等价（valid_from == created_at）、幂等重放、全新库路径、
I1/I2 模型默认值落锚。不触碰 lantai.storage.db 模块级 engine/get_session（conftest 绊线）。
本文件所有 execute 均为内联固定字面量或占位符绑定参数，无拼接执行。
"""

import sqlite3

import pytest
from sqlmodel import SQLModel, create_engine

from lantai.storage.db import apply_migrations


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _memoryitem_indexes(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {r[0] for r in rows if str(r[0]).startswith("ix_memoryitem_")}


def _make_v20_library(db_path) -> sqlite3.Connection:
    """构造真实 v20 形状文件库：按当前模型建表后，剥离 v21 新增列与其索引。

    这样 v20 列集与产品真实 schema 逐列一致（非手写近似 DDL），
    迁移升级面对被测代码完全真实。
    """
    import lantai.models.tables  # noqa: F401  # 注册表元数据

    engine = create_engine("sqlite:///" + str(db_path))
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(db_path))
    # 剥离 v21 三索引（valid_from/valid_to 在 v20 无索引，event_time 列尚不存在）
    conn.execute("DROP INDEX IF EXISTS ix_memoryitem_event_time")
    conn.execute("DROP INDEX IF EXISTS ix_memoryitem_valid_from")
    conn.execute("DROP INDEX IF EXISTS ix_memoryitem_valid_to")
    conn.execute("ALTER TABLE memoryitem DROP COLUMN event_time")
    conn.execute("ALTER TABLE memoryitem DROP COLUMN event_time_precision")
    # 回到 v20 数据面：清空 create_all 产生的结构行、置版本号
    conn.execute("DELETE FROM memoryitem")
    conn.execute("PRAGMA user_version = 20")
    conn.commit()
    return conn


@pytest.fixture()
def v20_file_db(tmp_path):
    """真实 v20 形状文件库：两行 valid_from NULL（不同 created_at）+ 一行已有 valid_from。"""
    conn = _make_v20_library(tmp_path / "v20.db")
    # 种子按 v20 真实 NOT NULL 列集补齐（memory_type/namespace/confidence/... 全部带模型默认值）
    # 元组序与下方 INSERT 列清单一致：id, content, valid_from, created_at, updated_at
    seeds = [
        (
            "m1",
            "content-m1",
            None,
            "2026-09-01 10:00:00.000000",
            "2026-09-01 10:00:00.000000",
        ),
        (
            "m2",
            "content-m2",
            None,
            "2026-09-02 11:00:00.000000",
            "2026-09-02 11:00:00.000000",
        ),
        (
            "m3",
            "content-m3",
            "2026-09-05 00:00:00.000000",
            "2026-09-03 12:00:00.000000",
            "2026-09-03 12:00:00.000000",
        ),
    ]
    conn.executemany(
        "INSERT INTO memoryitem (id, content, valid_from, created_at, updated_at,"
        " memory_type, namespace, confidence, reason, importance, tier, role, lane,"
        " domain, version, status, use_count, helpful_count, decay_score, decay_class,"
        " lifecycle_status) VALUES (?, ?, ?, ?, ?, 'text', 'default', 0.5, '', 0.5,"
        " 'working', 'observation', 'general', 'user', 1, 'active', 0, 0, 1.0,"
        " 'episodic', 'active')",
        seeds,
    )
    conn.commit()
    yield conn
    conn.close()


class TestGenglouMigrationV21:
    def test_upgrade_backfill_and_indexes(self, v20_file_db):
        """U5：v20 旧库升级——两新列就位、valid_from 回填 created_at、三索引建立。"""
        conn = v20_file_db
        assert _user_version(conn) == 20

        apply_migrations(conn)

        assert (
            _user_version(conn) == 24
        )  # 链已前移到 v24（票 07 沉潜过审 + ADR-0053 decided_at），幂等守卫保证 v21 段仍生效
        columns = {r[1] for r in conn.execute("PRAGMA table_info(memoryitem)").fetchall()}
        assert "event_time" in columns
        assert "event_time_precision" in columns

        # U5 回填等价：NULL 行回填为各自 created_at；已有值不被覆盖
        rows = conn.execute("SELECT id, valid_from, created_at FROM memoryitem").fetchall()
        fetched = {rid: (vf, created) for rid, vf, created in rows}
        assert fetched["m1"][0] == fetched["m1"][1]  # 回填 == created_at
        assert fetched["m2"][0] == fetched["m2"][1]
        assert fetched["m3"][0] == "2026-09-05 00:00:00.000000"  # 非 NULL 不覆盖
        null_count = conn.execute(
            "SELECT COUNT(*) FROM memoryitem WHERE valid_from IS NULL"
        ).fetchone()[0]
        assert null_count == 0

        # event_time 新列无预知数据，全 NULL（宁 miss 不猜）
        et_count = conn.execute(
            "SELECT COUNT(*) FROM memoryitem WHERE event_time IS NOT NULL"
        ).fetchone()[0]
        assert et_count == 0

        assert {
            "ix_memoryitem_event_time",
            "ix_memoryitem_valid_from",
            "ix_memoryitem_valid_to",
        } <= _memoryitem_indexes(conn)

    def test_idempotent_replay(self, v20_file_db):
        """迁移可重放：二次执行不炸、版本不回退、数据不再改写。"""
        conn = v20_file_db
        apply_migrations(conn)
        first_pass = conn.execute("SELECT id, valid_from FROM memoryitem ORDER BY id").fetchall()

        apply_migrations(conn)  # 重放

        assert _user_version(conn) == 24
        second_pass = conn.execute("SELECT id, valid_from FROM memoryitem ORDER BY id").fetchall()
        assert first_pass == second_pass

    def test_fresh_database_full_chain(self, tmp_path):
        """全新库：create_all（含 v21 列）→ apply_migrations 全链跑通，回填 0 行。"""
        import lantai.models.tables  # noqa: F401  # 注册表元数据

        engine = create_engine("sqlite:///" + str(tmp_path / "fresh.db"))
        SQLModel.metadata.create_all(engine)
        conn = engine.raw_connection()
        try:
            apply_migrations(conn)
            assert _user_version(conn) >= 21
            columns = {r[1] for r in conn.execute("PRAGMA table_info(memoryitem)").fetchall()}
            assert "event_time" in columns
            null_count = conn.execute(
                "SELECT COUNT(*) FROM memoryitem WHERE valid_from IS NULL"
            ).fetchone()[0]
            assert null_count == 0  # 空表，回填 0 行
        finally:
            conn.close()
            engine.dispose()

    def test_model_defaults_anchor_i1_i2(self):
        """I1 空侧 / I2 落锚：MemoryItem 默认构造 precision='' 且 valid_from 非空。"""
        from datetime import datetime

        from lantai.models.tables import MemoryItem

        item = MemoryItem(id="anchor-1", content="更漏落锚")
        assert item.event_time is None  # 宁 miss 不猜
        assert item.event_time_precision == ""  # I1：event_time 为空 ⇒ precision == ""
        assert isinstance(item.valid_from, datetime)  # I2：新写入 valid_from 非空
