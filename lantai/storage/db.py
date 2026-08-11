"""
数据库初始化与 FTS5 全文搜索
"""
import sqlite3
from sqlmodel import SQLModel, Session, create_engine
from lantai.core.settings import settings
from lantai.core.logger import logger
from lantai.storage.fts import init_fts

# busy_timeout=30s：pre_compress daemon 线程与请求线程并发写库时避免
# 瞬时 "database is locked" 静默丢数据
engine = create_engine(settings.DATABASE_URL, echo=False,
                       connect_args={"timeout": 30})

# ── Schema 版本化（v0.6 Ticket 01，借鉴 aiduMEI v18.3 Fast-Update）──
# PRAGMA user_version 记录数据库结构版本；未版本化库（全新库或 v0.5 及以前
# 老库）自动基线为 v1，增量补丁按版本号依次执行。ALTER TABLE ADD COLUMN 为
# 毫秒级操作，代码更新与数据重构解耦，异常只记日志不阻断启动（降级而非崩溃）。
CURRENT_SCHEMA_VERSION = 8


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """列缺失时 ADD COLUMN；列已存在或表不存在均幂等跳过。"""
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column in cols:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info("迁移：%s.%s 已补充", table, column)
    except Exception as exc:
        logger.warning("迁移跳过 %s.%s: %s", table, column, exc)


def apply_migrations(conn) -> None:
    """基于 user_version 的增量迁移链（无损升级，幂等，异常不阻断启动）。"""
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

        # 未版本化库（全新库或老库）基线置为 v1
        if user_version == 0:
            user_version = 1
            conn.execute("PRAGMA user_version = 1")
            conn.commit()

        # v1 -> v2：v0.4/v0.5 累积的三个幂等列迁移
        if user_version < 2:
            _ensure_column(conn, "memoryitem", "decay_class",
                           "TEXT DEFAULT 'episodic'")
            _ensure_column(conn, "retrieval_event", "is_system_noise",
                           "BOOLEAN DEFAULT 0")
            _ensure_column(conn, "memorycandidate", "review_due_at", "DATETIME")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
            logger.info("数据库增量迁移 v2 完成 ✅")

        # v2 -> v3（ADR-0012 scene 聚合层）：memoryitem.scene_id + memoryscene 表
        if user_version < 3:
            _ensure_column(conn, "memoryitem", "scene_id", "TEXT")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memoryscene ("
                "id TEXT PRIMARY KEY, name TEXT, summary TEXT, "
                "heat INTEGER DEFAULT 0, member_count INTEGER DEFAULT 0, "
                "created_at DATETIME, updated_at DATETIME)")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='memoryitem'").fetchone():
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_scene_id "
                    "ON memoryitem (scene_id)")
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            logger.info("数据库增量迁移 v3 完成（scene 聚合层）")
        # v3 -> v4（可观测性）：retrieval_event 补 scene_ids / estimated_tokens
        if user_version < 4:
            _ensure_column(conn, "retrieval_event", "scene_ids", "TEXT")
            _ensure_column(conn, "retrieval_event", "estimated_tokens", "INTEGER DEFAULT 0")
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            logger.info("数据库增量迁移 v4 完成（可观测性）")
        # v4 -> v5（scene 增量聚类）：memoryscene 补 centroid 质心
        if user_version < 5:
            _ensure_column(conn, "memoryscene", "centroid", "TEXT")
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
            logger.info("数据库增量迁移 v5 完成（scene 增量聚类质心）")
        # v5 -> v6（provenance 提取来源）：candidate/proposal/memoryitem 补 provenance
        if user_version < 6:
            _ensure_column(conn, "memorycandidate", "provenance", "TEXT")
            _ensure_column(conn, "memoryproposal", "provenance", "TEXT")
            _ensure_column(conn, "memoryitem", "provenance", "TEXT")
            conn.execute("PRAGMA user_version = 6")
            conn.commit()
            logger.info("数据库增量迁移 v6 完成（provenance 提取来源）")
        # v6 -> v7（反思可测量）：memoryproposal 补 decision_reason 裁决原因
        if user_version < 7:
            _ensure_column(conn, "memoryproposal", "decision_reason",
                           "TEXT DEFAULT ''")
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            logger.info("数据库增量迁移 v7 完成（裁决原因）")
        # v7 -> v8（观察期保底）：scheduler_run 记录各 worker 上次运行时间（/stats 持久化 + 启动补跑）
        if user_version < 8:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scheduler_run ("
                "name TEXT PRIMARY KEY, last_run_utc TEXT NOT NULL)")
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
            logger.info("数据库增量迁移 v8 完成（worker 运行记录持久化）")
        # 未来版本在此追加：if user_version < 9: ...
    except Exception as exc:
        logger.error("数据库增量迁移异常（服务继续启动）: %s", exc)


def init_db():
    from lantai.models import tables  # noqa
    SQLModel.metadata.create_all(engine)
    # 幂等列迁移：老库缺列时 create_all 不会加列，统一走 user_version 增量链
    conn = None
    try:
        conn = engine.raw_connection()
        apply_migrations(conn)
    finally:
        if conn is not None:
            conn.close()
    # 初始化 FTS5
    conn = engine.raw_connection()
    init_fts(conn)


def get_session() -> Session:
    return Session(engine)

