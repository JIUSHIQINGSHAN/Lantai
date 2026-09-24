"""
数据库初始化与 FTS5 全文搜索
"""

from sqlmodel import Session, SQLModel, create_engine

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.storage.fts import init_fts

# busy_timeout=30s：pre_compress daemon 线程与请求线程并发写库时避免
# 瞬时 "database is locked" 静默丢数据
engine = create_engine(settings.DATABASE_URL, echo=False, connect_args={"timeout": 30})

# ── Schema 版本化（v0.6 Ticket 01，借鉴 aiduMEI v18.3 Fast-Update）──
# PRAGMA user_version 记录数据库结构版本；未版本化库（全新库或 v0.5 及以前
# 老库）自动基线为 v1，增量补丁按版本号依次执行。ALTER TABLE ADD COLUMN 为
# 毫秒级操作，代码更新与数据重构解耦，异常只记日志不阻断启动（降级而非崩溃）。
CURRENT_SCHEMA_VERSION = 22


def _has_column(conn, table: str, column: str) -> bool:
    """查询列是否存在；表不存在视为 True（迁移链不建表，建表归 create_all）。

    DDL 迁移一律在调用点写**固定字面量**（SQLite ALTER TABLE 不支持
    绑定参数，防注入整改后不再保留可变 DDL 的执行入口）；列存在性
    检查走 table-valued PRAGMA 的参数绑定查询。"""
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            return True
        return bool(
            conn.execute(
                "SELECT name FROM pragma_table_info(?) WHERE name = ?", (table, column)
            ).fetchall()
        )
    except Exception as exc:
        logger.warning("列检查跳过 %s.%s: %s", table, column, exc)
        return False


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
            if not _has_column(conn, "memoryitem", "decay_class"):
                conn.execute(
                    "ALTER TABLE memoryitem ADD COLUMN decay_class TEXT DEFAULT 'episodic'"
                )
            if not _has_column(conn, "retrieval_event", "is_system_noise"):
                conn.execute(
                    "ALTER TABLE retrieval_event ADD COLUMN is_system_noise BOOLEAN DEFAULT 0"
                )
            if not _has_column(conn, "memorycandidate", "review_due_at"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN review_due_at DATETIME")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
            logger.info("数据库增量迁移 v2 完成 ✅")

        # v2 -> v3（ADR-0012 scene 聚合层）：memoryitem.scene_id + memoryscene 表
        if user_version < 3:
            if not _has_column(conn, "memoryitem", "scene_id"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN scene_id TEXT")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memoryscene ("
                "id TEXT PRIMARY KEY, name TEXT, summary TEXT, "
                "heat INTEGER DEFAULT 0, member_count INTEGER DEFAULT 0, "
                "created_at DATETIME, updated_at DATETIME)"
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memoryitem'"
            ).fetchone():
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_scene_id ON memoryitem (scene_id)"
                )
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            logger.info("数据库增量迁移 v3 完成（scene 聚合层）")
        # v3 -> v4（可观测性）：retrieval_event 补 scene_ids / estimated_tokens
        if user_version < 4:
            if not _has_column(conn, "retrieval_event", "scene_ids"):
                conn.execute("ALTER TABLE retrieval_event ADD COLUMN scene_ids TEXT")
            if not _has_column(conn, "retrieval_event", "estimated_tokens"):
                conn.execute(
                    "ALTER TABLE retrieval_event ADD COLUMN estimated_tokens INTEGER DEFAULT 0"
                )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            logger.info("数据库增量迁移 v4 完成（可观测性）")
        # v4 -> v5（scene 增量聚类）：memoryscene 补 centroid 质心
        if user_version < 5:
            if not _has_column(conn, "memoryscene", "centroid"):
                conn.execute("ALTER TABLE memoryscene ADD COLUMN centroid TEXT")
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
            logger.info("数据库增量迁移 v5 完成（scene 增量聚类质心）")
        # v5 -> v6（provenance 提取来源）：candidate/proposal/memoryitem 补 provenance
        if user_version < 6:
            if not _has_column(conn, "memorycandidate", "provenance"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN provenance TEXT")
            if not _has_column(conn, "memoryproposal", "provenance"):
                conn.execute("ALTER TABLE memoryproposal ADD COLUMN provenance TEXT")
            if not _has_column(conn, "memoryitem", "provenance"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN provenance TEXT")
            conn.execute("PRAGMA user_version = 6")
            conn.commit()
            logger.info("数据库增量迁移 v6 完成（provenance 提取来源）")
        # v6 -> v7（反思可测量）：memoryproposal 补 decision_reason 裁决原因
        if user_version < 7:
            if not _has_column(conn, "memoryproposal", "decision_reason"):
                conn.execute(
                    "ALTER TABLE memoryproposal ADD COLUMN decision_reason TEXT DEFAULT ''"
                )
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            logger.info("数据库增量迁移 v7 完成（裁决原因）")
        # v7 -> v8（观察期保底）：scheduler_run 记录各 worker 上次运行时间（/stats 持久化 + 启动补跑）
        if user_version < 8:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scheduler_run ("
                "name TEXT PRIMARY KEY, last_run_utc TEXT NOT NULL)"
            )
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
            logger.info("数据库增量迁移 v8 完成（worker 运行记录持久化）")
        # v8 -> v9（v0.7 树状图谱 + 技能结晶）：memoryitem.tree_path + memorynode/skillcrystal 表
        if user_version < 9:
            if not _has_column(conn, "memoryitem", "tree_path"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN tree_path TEXT")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memorynode ("
                "id TEXT PRIMARY KEY, parent_id TEXT, name TEXT, "
                "node_path TEXT UNIQUE, depth INTEGER DEFAULT 0, "
                "description TEXT DEFAULT '', namespace TEXT DEFAULT 'default', "
                "created_at DATETIME)"
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memoryitem'"
            ).fetchone():
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_tree_path ON memoryitem (tree_path)"
                )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS skillcrystal ("
                "id TEXT PRIMARY KEY, skill_name TEXT UNIQUE, trigger_rule TEXT, "
                "procedure TEXT, source_lanes TEXT, sample_keys TEXT, "
                "hit_count INTEGER DEFAULT 1, candidate_count INTEGER DEFAULT 0, "
                "status TEXT DEFAULT 'candidate', decision_reason TEXT DEFAULT '', "
                "created_at DATETIME, updated_at DATETIME)"
            )
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
                "discarded INTEGER DEFAULT 0, error TEXT DEFAULT '')"
            )
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
            logger.info("数据库增量迁移 v10 完成（反思运行记录）")
        # v10 -> v11（裁决失败留痕）：reflect_run 补 rejecter_failed 裁决 LLM 失败次数
        if user_version < 11:
            if not _has_column(conn, "reflect_run", "rejecter_failed"):
                conn.execute(
                    "ALTER TABLE reflect_run ADD COLUMN rejecter_failed INTEGER DEFAULT 0"
                )
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
            logger.info("数据库增量迁移 v11 完成（裁决失败留痕）")
        # v11 -> v12（底本五段会话快照，ADR-0021）：session_checkpoint 表
        if user_version < 12:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_checkpoint ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT NOT NULL, block_key TEXT NOT NULL, "
                "content TEXT NOT NULL, created_at DATETIME)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_checkpoint_session "
                "ON session_checkpoint(session_id)"
            )
            conn.execute("PRAGMA user_version = 12")
            conn.commit()
            logger.info("数据库增量迁移 v12 完成（底本五段会话快照）")
        # v12 -> v13（观察期来源可审计）：区分定时与手动反思；旧数据保守标 unknown
        if user_version < 13:
            if not _has_column(conn, "reflect_run", "source"):
                conn.execute("ALTER TABLE reflect_run ADD COLUMN source TEXT DEFAULT 'unknown'")
            conn.execute("PRAGMA user_version = 13")
            conn.commit()
            logger.info("数据库增量迁移 v13 完成（反思运行来源）")
        # v13 -> v14（案牍控制台）：候选延期与单步撤销留痕
        if user_version < 14:
            if not _has_column(conn, "memorycandidate", "deferred_at"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN deferred_at DATETIME")
            if not _has_column(conn, "memorycandidate", "previous_review_due_at"):
                conn.execute(
                    "ALTER TABLE memorycandidate ADD COLUMN previous_review_due_at DATETIME"
                )
            if not _has_column(conn, "memorycandidate", "defer_count"):
                conn.execute(
                    "ALTER TABLE memorycandidate ADD COLUMN defer_count INTEGER DEFAULT 0"
                )
            if not _has_column(conn, "memorycandidate", "defer_reason"):
                conn.execute(
                    "ALTER TABLE memorycandidate ADD COLUMN defer_reason TEXT DEFAULT ''"
                )
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
                "updated_at DATETIME)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_profile_name "
                "ON persona_profile(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_persona_profile_active "
                "ON persona_profile(is_active)"
            )
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
        # v16 -> v17（辨域 User-Session-Agent 三维硬隔离，ADR-0034）：memoryitem.domain 列
        if user_version < 17:
            if not _has_column(conn, "memoryitem", "domain"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN domain TEXT DEFAULT 'user'")
            try:
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                if "memoryitem" in tables:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_memoryitem_domain ON memoryitem(domain)"
                    )
            except Exception:
                pass
            conn.execute("PRAGMA user_version = 17")
            conn.commit()
        # v17 -> v18: Add ownership fields to multiple tables
        if user_version < 18:
            # v18 身份列：九张表各补 tenant/user/agent/session 四列（固定字面量展开）
            if not _has_column(conn, "rawdocument", "tenant_id"):
                conn.execute("ALTER TABLE rawdocument ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "rawdocument", "user_id"):
                conn.execute("ALTER TABLE rawdocument ADD COLUMN user_id TEXT")
            if not _has_column(conn, "rawdocument", "agent_id"):
                conn.execute("ALTER TABLE rawdocument ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "rawdocument", "session_id"):
                conn.execute("ALTER TABLE rawdocument ADD COLUMN session_id TEXT")
            if not _has_column(conn, "documentchunk", "tenant_id"):
                conn.execute("ALTER TABLE documentchunk ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "documentchunk", "user_id"):
                conn.execute("ALTER TABLE documentchunk ADD COLUMN user_id TEXT")
            if not _has_column(conn, "documentchunk", "agent_id"):
                conn.execute("ALTER TABLE documentchunk ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "documentchunk", "session_id"):
                conn.execute("ALTER TABLE documentchunk ADD COLUMN session_id TEXT")
            if not _has_column(conn, "memorycandidate", "tenant_id"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "memorycandidate", "user_id"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN user_id TEXT")
            if not _has_column(conn, "memorycandidate", "agent_id"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "memorycandidate", "session_id"):
                conn.execute("ALTER TABLE memorycandidate ADD COLUMN session_id TEXT")
            if not _has_column(conn, "memoryitem", "tenant_id"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "memoryitem", "user_id"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN user_id TEXT")
            if not _has_column(conn, "memoryitem", "agent_id"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "memoryitem", "session_id"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN session_id TEXT")
            if not _has_column(conn, "memoryproposal", "tenant_id"):
                conn.execute("ALTER TABLE memoryproposal ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "memoryproposal", "user_id"):
                conn.execute("ALTER TABLE memoryproposal ADD COLUMN user_id TEXT")
            if not _has_column(conn, "memoryproposal", "agent_id"):
                conn.execute("ALTER TABLE memoryproposal ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "memoryproposal", "session_id"):
                conn.execute("ALTER TABLE memoryproposal ADD COLUMN session_id TEXT")
            if not _has_column(conn, "memoryedge", "tenant_id"):
                conn.execute("ALTER TABLE memoryedge ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "memoryedge", "user_id"):
                conn.execute("ALTER TABLE memoryedge ADD COLUMN user_id TEXT")
            if not _has_column(conn, "memoryedge", "agent_id"):
                conn.execute("ALTER TABLE memoryedge ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "memoryedge", "session_id"):
                conn.execute("ALTER TABLE memoryedge ADD COLUMN session_id TEXT")
            if not _has_column(conn, "session_checkpoint", "tenant_id"):
                conn.execute("ALTER TABLE session_checkpoint ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "session_checkpoint", "user_id"):
                conn.execute("ALTER TABLE session_checkpoint ADD COLUMN user_id TEXT")
            if not _has_column(conn, "session_checkpoint", "agent_id"):
                conn.execute("ALTER TABLE session_checkpoint ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "session_checkpoint", "session_id"):
                conn.execute("ALTER TABLE session_checkpoint ADD COLUMN session_id TEXT")
            if not _has_column(conn, "session_scratchpad", "tenant_id"):
                conn.execute("ALTER TABLE session_scratchpad ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "session_scratchpad", "user_id"):
                conn.execute("ALTER TABLE session_scratchpad ADD COLUMN user_id TEXT")
            if not _has_column(conn, "session_scratchpad", "agent_id"):
                conn.execute("ALTER TABLE session_scratchpad ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "session_scratchpad", "session_id"):
                conn.execute("ALTER TABLE session_scratchpad ADD COLUMN session_id TEXT")
            if not _has_column(conn, "persona_profile", "tenant_id"):
                conn.execute("ALTER TABLE persona_profile ADD COLUMN tenant_id TEXT")
            if not _has_column(conn, "persona_profile", "user_id"):
                conn.execute("ALTER TABLE persona_profile ADD COLUMN user_id TEXT")
            if not _has_column(conn, "persona_profile", "agent_id"):
                conn.execute("ALTER TABLE persona_profile ADD COLUMN agent_id TEXT")
            if not _has_column(conn, "persona_profile", "session_id"):
                conn.execute("ALTER TABLE persona_profile ADD COLUMN session_id TEXT")

            # domain to memorycandidate
            if not _has_column(conn, "memorycandidate", "domain"):
                conn.execute(
                    "ALTER TABLE memorycandidate ADD COLUMN domain TEXT DEFAULT 'user'"
                )

            conn.execute("PRAGMA user_version = 18")
            conn.commit()
            logger.info("Migrated v18: Added ownership fields")

        # v18 -> v19: 认知底层字段 (memorycandidate.role, memoryitem.promotion_trace)
        if user_version < 19:
            if not _has_column(conn, "memorycandidate", "role"):
                conn.execute(
                    "ALTER TABLE memorycandidate ADD COLUMN role TEXT DEFAULT 'observation'"
                )
            if not _has_column(conn, "memoryitem", "promotion_trace"):
                conn.execute(
                    "ALTER TABLE memoryitem ADD COLUMN promotion_trace TEXT DEFAULT '{}'"
                )
            conn.execute("PRAGMA user_version = 19")
            conn.commit()
            logger.info("Migrated v19: Cognitive fields (role, promotion_trace)")

        # v19 -> v20: Knowledge Lifecycle 状态机字段 (v0.4)
        if user_version < 20:
            if not _has_column(conn, "memoryitem", "lifecycle_status"):
                conn.execute(
                    "ALTER TABLE memoryitem ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'"
                )
            if not _has_column(conn, "memoryitem", "superseded_by"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN superseded_by TEXT")
            if not _has_column(conn, "memoryitem", "weakened_at"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN weakened_at DATETIME")
            if not _has_column(conn, "memoryitem", "superseded_at"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN superseded_at DATETIME")
            if not _has_column(conn, "memoryitem", "retired_at"):
                conn.execute("ALTER TABLE memoryitem ADD COLUMN retired_at DATETIME")
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_lifecycle_status ON memoryitem(lifecycle_status)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memoryitem_superseded_by ON memoryitem(superseded_by)"
                )
            except Exception:
                pass
            conn.execute("PRAGMA user_version = 20")
            conn.commit()
            logger.info("Migrated v20: Knowledge Lifecycle fields")

        # v20 -> v21: 更漏（ADR-0048）双时间轴——event_time 两新列 + valid_from 回填 + 时效三索引
        if user_version < 21:
            # 空库/无表场景（迁移测试建账用例）：表由 create_all 负责，迁移只记账不建表
            has_memoryitem = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table'"
                    " AND name = 'memoryitem'"
                ).fetchone()
            )
            if has_memoryitem:
                # DDL 固定字面量（SQLite ALTER TABLE 不支持绑定参数），_has_column 幂等守卫
                if not _has_column(conn, "memoryitem", "event_time"):
                    conn.execute("ALTER TABLE memoryitem ADD COLUMN event_time DATETIME")
                if not _has_column(conn, "memoryitem", "event_time_precision"):
                    conn.execute(
                        "ALTER TABLE memoryitem ADD COLUMN event_time_precision TEXT DEFAULT ''"
                    )
                # 回填（spec §5.3）：valid_from 语义必填，存量 NULL 以 created_at 近似——
                # 当前态下 created_at ≤ now 恒真，行为与 NULL 等价零回归；as-of 判定
                # 「any-of + unknown 软放行」使回填无法制造新的误排除。DML 常量字面量，无注入面。
                # 双列守卫：极老库（缺 valid_from/created_at 任一列）跳过回填，宁欠不炸。
                if _has_column(conn, "memoryitem", "valid_from") and _has_column(
                    conn, "memoryitem", "created_at"
                ):
                    conn.execute(
                        "UPDATE memoryitem SET valid_from = created_at WHERE valid_from IS NULL"
                    )
                    try:
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS ix_memoryitem_event_time ON memoryitem(event_time)"
                        )
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS ix_memoryitem_valid_from ON memoryitem(valid_from)"
                        )
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS ix_memoryitem_valid_to ON memoryitem(valid_to)"
                        )
                    except Exception:
                        pass
            conn.execute("PRAGMA user_version = 21")
            conn.commit()
            logger.info("Migrated v21: Genglou bi-temporal event time (ADR-0048)")

        # v21 -> v22: 回执链一等化（ADR-0049）——request_id + receipt_status + receipt_at
        if user_version < 22:
            has_re = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table'"
                    " AND name = 'retrieval_event'"
                ).fetchone()
            )
            if has_re:
                if not _has_column(conn, "retrieval_event", "request_id"):
                    conn.execute("ALTER TABLE retrieval_event ADD COLUMN request_id TEXT")
                if not _has_column(conn, "retrieval_event", "receipt_status"):
                    conn.execute(
                        "ALTER TABLE retrieval_event ADD COLUMN receipt_status TEXT DEFAULT 'pending'"
                    )
                if not _has_column(conn, "retrieval_event", "receipt_at"):
                    conn.execute("ALTER TABLE retrieval_event ADD COLUMN receipt_at DATETIME")
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS ix_retrieval_event_request_id"
                        " ON retrieval_event(request_id)"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS ix_retrieval_event_receipt_status"
                        " ON retrieval_event(receipt_status)"
                    )
                except Exception:
                    pass
            conn.execute("PRAGMA user_version = 22")
            conn.commit()
            logger.info("Migrated v22: receipt chain (ADR-0049)")

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
