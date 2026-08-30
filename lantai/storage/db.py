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
CURRENT_SCHEMA_VERSION = 16


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
        # v8 -> v9（v0.7 树状图谱 + 技能结晶）：memoryitem.tree_path + memorynode/skillcrystal 表
        if user_version < 9:
            _ensure_column(conn, "memoryitem", "tree_path", "TEXT")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memorynode ("
                "id TEXT PRIMARY KEY, parent_id TEXT, name TEXT, "
                "node_path TEXT UNIQUE, depth INTEGER DEFAULT 0, "
                "description TEXT DEFAULT '', namespace TEXT DEFAULT 'default', "
                "created_at DATETIME)")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='memoryitem'").fetchone():
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_tree_path "
                    "ON memoryitem (tree_path)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS skillcrystal ("
                "id TEXT PRIMARY KEY, skill_name TEXT UNIQUE, trigger_rule TEXT, "
                "procedure TEXT, source_lanes TEXT, sample_keys TEXT, "
                "hit_count INTEGER DEFAULT 1, candidate_count INTEGER DEFAULT 0, "
                "status TEXT DEFAULT 'candidate', decision_reason TEXT DEFAULT '', "
                "created_at DATETIME, updated_at DATETIME)")
            conn.execute("PRAGMA user_version = 9")
            conn.commit()
            logger.info("数据库增量迁移 v9 完成（树状图谱 + 技能结晶）")
        # v9 -> v10（反思运行可审计）：reflect_run 记录每次运行的水位/跳过/产出/LLM 失败
        if user_version < 10:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reflect_run ("
                "id TEXT PRIMARY KEY, run_at DATETIME, "
                "waterline REAL DEFAULT 0, skipped TEXT DEFAULT '', "
                "curate_failed INTEGER DEFAULT 0, "
                "health_before TEXT, health_after TEXT, "
                "proposals_created INTEGER DEFAULT 0, "
                "auto_applied INTEGER DEFAULT 0, pending INTEGER DEFAULT 0, "
                "discarded INTEGER DEFAULT 0, error TEXT DEFAULT '')")
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
            logger.info("数据库增量迁移 v10 完成（反思运行记录）")
        # v10 -> v11（裁决失败留痕）：reflect_run 补 rejecter_failed 裁决 LLM 失败次数
        if user_version < 11:
            _ensure_column(conn, "reflect_run", "rejecter_failed",
                           "INTEGER DEFAULT 0")
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
            logger.info("数据库增量迁移 v11 完成（裁决失败留痕）")
        # v11 -> v12（底本五段会话快照，ADR-0021）：session_checkpoint 表
        if user_version < 12:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_checkpoint ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT NOT NULL, block_key TEXT NOT NULL, "
                "content TEXT NOT NULL, created_at DATETIME)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_checkpoint_session "
                         "ON session_checkpoint(session_id)")
            conn.execute("PRAGMA user_version = 12")
            conn.commit()
            logger.info("数据库增量迁移 v12 完成（底本五段会话快照）")
        # v12 -> v13（观察期来源可审计）：区分定时与手动反思；旧数据保守标 unknown
        if user_version < 13:
            _ensure_column(conn, "reflect_run", "source",
                           "TEXT DEFAULT 'unknown'")
            conn.execute("PRAGMA user_version = 13")
            conn.commit()
            logger.info("数据库增量迁移 v13 完成（反思运行来源）")
        # v13 -> v14（案牍控制台）：候选延期与单步撤销留痕
        if user_version < 14:
            _ensure_column(conn, "memorycandidate", "deferred_at", "DATETIME")
            _ensure_column(conn, "memorycandidate", "previous_review_due_at", "DATETIME")
            _ensure_column(conn, "memorycandidate", "defer_count", "INTEGER DEFAULT 0")
            _ensure_column(conn, "memorycandidate", "defer_reason", "TEXT DEFAULT ''")
            conn.execute("PRAGMA user_version = 14")
            conn.commit()
            logger.info("数据库增量迁移 v14 完成（候选延期留痕）")
        # v14 -> v15（器识 Persona 人格基座，ADR-0029）：persona_profile 表
        if user_version < 15:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS persona_profile ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "is_active BOOLEAN DEFAULT 0, "
                "linguistic_style TEXT DEFAULT '', "
                "guidelines TEXT DEFAULT '', "
                "epistemic_facts TEXT DEFAULT '', "
                "created_at DATETIME, "
                "updated_at DATETIME)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_profile_name "
                         "ON persona_profile(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_persona_profile_active "
                         "ON persona_profile(is_active)")
            conn.execute("PRAGMA user_version = 15")
            conn.commit()
            logger.info("数据库增量迁移 v15 完成（器识 Persona 人格基座）")
        # v15 -> v16（札记 Session Scratchpad，ADR-0032）：session_scratchpad 表
        if user_version < 16:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_scratchpad ("
                "session_id TEXT PRIMARY KEY, "
                "content TEXT DEFAULT '', "
                "created_at DATETIME, "
                "updated_at DATETIME)"
            )
            conn.execute("PRAGMA user_version = 16")
            conn.commit()
            logger.info("数据库增量迁移 v16 完成（札记 Session Scratchpad）")


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
