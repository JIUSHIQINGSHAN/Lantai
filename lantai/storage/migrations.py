"""数据库增量迁移链（票 `.scratch/migration-chain/01`，自 `storage/db.py` 拆出）。

每个 `_migrate_vN(conn)` 是原来 `apply_migrations` 里的一个线性 if 块，
SQL 与注释逐字未改，只改包裹结构：

- **逐块隔离**：一块抛异常不再中断整条链（原来 25 块里 19 块无保护，
  一步失败后面全部静默跳过——实证见 `probe_24_chain_break.py`）。
- **version 只在块成功后 +1**：失败的块下次启动会重试（瞬时失败自愈）。
- **失败留痕**：进 `_MIGRATION_FAILURES`，由 `/health/deep` 的
  `checks["migrations"]` 报出来。
"""

from lantai.core.logger import logger


def _has_column(conn, table: str, column: str) -> bool:
    """查询列是否存在；表不存在视为 True（迁移链不建表，建表归 create_all）。

    DDL 迁移一律在调用点写**固定字面量**（SQLite ALTER TABLE 不支持
    绑定参数，防注入整改后不再保留可变 DDL 的执行入口）；列存在性
    检查走 table-valued PRAGMA 的参数绑定查询。

    随迁移拆分从 `db.py` 移入本模块——25 个迁移块是它的唯一使用者，
    放在这里依赖单向（db.py 导本模块，本模块不导 db.py）。
    """
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


# 单一真源：迁移链的目标版本。没有它就没法判断「跑到头了没有」——
# 原来最高版本硬编码散落在 25 个 `if user_version < N` 里。
TARGET_SCHEMA_VERSION = 42

# 本次进程内记录到的失败跳（version, error）。健康检查与运维查询用。
_MIGRATION_FAILURES: list[dict] = []


def get_migration_failures() -> list[dict]:
    """返回本次启动记录到的迁移失败（空列表表示全部成功）。"""
    return list(_MIGRATION_FAILURES)


def _migrate_v2(conn) -> None:
    """v1 -> v2：v0.4/v0.5 累积的三个幂等列迁移"""
    if not _has_column(conn, "memoryitem", "decay_class"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN decay_class TEXT DEFAULT 'episodic'")
    if not _has_column(conn, "retrieval_event", "is_system_noise"):
        conn.execute("ALTER TABLE retrieval_event ADD COLUMN is_system_noise BOOLEAN DEFAULT 0")
    if not _has_column(conn, "memorycandidate", "review_due_at"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN review_due_at DATETIME")
    logger.info("数据库增量迁移 v2 完成 ✅")


def _migrate_v3(conn) -> None:
    """v2 -> v3（ADR-0012 scene 聚合层）：memoryitem.scene_id + memoryscene 表"""
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
        conn.execute("CREATE INDEX IF NOT EXISTS ix_memoryitem_scene_id ON memoryitem (scene_id)")
    logger.info("数据库增量迁移 v3 完成（scene 聚合层）")


def _migrate_v4(conn) -> None:
    """v3 -> v4（可观测性）：retrieval_event 补 scene_ids / estimated_tokens"""
    if not _has_column(conn, "retrieval_event", "scene_ids"):
        conn.execute("ALTER TABLE retrieval_event ADD COLUMN scene_ids TEXT")
    if not _has_column(conn, "retrieval_event", "estimated_tokens"):
        conn.execute("ALTER TABLE retrieval_event ADD COLUMN estimated_tokens INTEGER DEFAULT 0")
    logger.info("数据库增量迁移 v4 完成（可观测性）")


def _migrate_v5(conn) -> None:
    """v4 -> v5（scene 增量聚类）：memoryscene 补 centroid 质心"""
    if not _has_column(conn, "memoryscene", "centroid"):
        conn.execute("ALTER TABLE memoryscene ADD COLUMN centroid TEXT")
    logger.info("数据库增量迁移 v5 完成（scene 增量聚类质心）")


def _migrate_v6(conn) -> None:
    """v5 -> v6（provenance 提取来源）：candidate/proposal/memoryitem 补 provenance"""
    if not _has_column(conn, "memorycandidate", "provenance"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN provenance TEXT")
    if not _has_column(conn, "memoryproposal", "provenance"):
        conn.execute("ALTER TABLE memoryproposal ADD COLUMN provenance TEXT")
    if not _has_column(conn, "memoryitem", "provenance"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN provenance TEXT")
    logger.info("数据库增量迁移 v6 完成（provenance 提取来源）")


def _migrate_v7(conn) -> None:
    """v6 -> v7（反思可测量）：memoryproposal 补 decision_reason 裁决原因"""
    if not _has_column(conn, "memoryproposal", "decision_reason"):
        conn.execute("ALTER TABLE memoryproposal ADD COLUMN decision_reason TEXT DEFAULT ''")
    logger.info("数据库增量迁移 v7 完成（裁决原因）")


def _migrate_v8(conn) -> None:
    """v7 -> v8（观察期保底）：scheduler_run 记录各 worker 上次运行时间（/stats 持久化 + 启动补跑）"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scheduler_run ("
        "name TEXT PRIMARY KEY, last_run_utc TEXT NOT NULL)"
    )
    logger.info("数据库增量迁移 v8 完成（worker 运行记录持久化）")


def _migrate_v9(conn) -> None:
    """v8 -> v9（v0.7 树状图谱 + 技能结晶）：memoryitem.tree_path + memorynode/skillcrystal 表"""
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
        conn.execute("CREATE INDEX IF NOT EXISTS ix_memoryitem_tree_path ON memoryitem (tree_path)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skillcrystal ("
        "id TEXT PRIMARY KEY, skill_name TEXT UNIQUE, trigger_rule TEXT, "
        "procedure TEXT, source_lanes TEXT, sample_keys TEXT, "
        "hit_count INTEGER DEFAULT 1, candidate_count INTEGER DEFAULT 0, "
        "status TEXT DEFAULT 'candidate', decision_reason TEXT DEFAULT '', "
        "created_at DATETIME, updated_at DATETIME)"
    )
    logger.info("数据库增量迁移 v9 完成（树状图谱 + 技能结晶）")


def _migrate_v10(conn) -> None:
    """v9 -> v10（反思运行可审计）：reflect_run 记录每次运行的水位/跳过/产出/LLM 失败"""
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
    logger.info("数据库增量迁移 v10 完成（反思运行记录）")


def _migrate_v11(conn) -> None:
    """v10 -> v11（裁决失败留痕）：reflect_run 补 rejecter_failed 裁决 LLM 失败次数"""
    if not _has_column(conn, "reflect_run", "rejecter_failed"):
        conn.execute("ALTER TABLE reflect_run ADD COLUMN rejecter_failed INTEGER DEFAULT 0")
    logger.info("数据库增量迁移 v11 完成（裁决失败留痕）")


def _migrate_v12(conn) -> None:
    """v11 -> v12（底本五段会话快照，ADR-0021）：session_checkpoint 表"""
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
    logger.info("数据库增量迁移 v12 完成（底本五段会话快照）")


def _migrate_v13(conn) -> None:
    """v12 -> v13（观察期来源可审计）：区分定时与手动反思；旧数据保守标 unknown"""
    if not _has_column(conn, "reflect_run", "source"):
        conn.execute("ALTER TABLE reflect_run ADD COLUMN source TEXT DEFAULT 'unknown'")
    logger.info("数据库增量迁移 v13 完成（反思运行来源）")


def _migrate_v14(conn) -> None:
    """v13 -> v14（案牍控制台）：候选延期与单步撤销留痕"""
    if not _has_column(conn, "memorycandidate", "deferred_at"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN deferred_at DATETIME")
    if not _has_column(conn, "memorycandidate", "previous_review_due_at"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN previous_review_due_at DATETIME")
    if not _has_column(conn, "memorycandidate", "defer_count"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN defer_count INTEGER DEFAULT 0")
    if not _has_column(conn, "memorycandidate", "defer_reason"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN defer_reason TEXT DEFAULT ''")
    logger.info("数据库增量迁移 v14 完成（候选延期留痕）")


def _migrate_v15(conn) -> None:
    """v14 -> v15（器识 Persona 人格基座，ADR-0029）：persona_profile 表"""
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
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_profile_name ON persona_profile(name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_persona_profile_active ON persona_profile(is_active)"
    )
    logger.info("数据库增量迁移 v15 完成（器识 Persona 人格基座）")


def _migrate_v16(conn) -> None:
    """v15 -> v16（札记 Session Scratchpad，ADR-0032）：session_scratchpad 表"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_scratchpad ("
        "session_id TEXT PRIMARY KEY, "
        "content TEXT DEFAULT '', "
        "created_at DATETIME, "
        "updated_at DATETIME)"
    )
    logger.info("数据库增量迁移 v16 完成（札记 Session Scratchpad）")


def _migrate_v17(conn) -> None:
    """v16 -> v17（辨域 User-Session-Agent 三维硬隔离，ADR-0034）：memoryitem.domain 列"""
    if not _has_column(conn, "memoryitem", "domain"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN domain TEXT DEFAULT 'user'")
    try:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        if "memoryitem" in tables:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memoryitem_domain ON memoryitem(domain)")
    except Exception as exc:
        # 索引缺失不阻断迁移（老库必须能起来），但必须留痕——
        # 静默缺失会让后续查询悄悄退化且无从归因（票 .scratch/migration-observability/01）
        logger.warning("迁移 v17 索引创建跳过 idx_memoryitem_domain: %s", exc)


def _migrate_v18(conn) -> None:
    """v17 -> v18: Add ownership fields to multiple tables"""
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
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN domain TEXT DEFAULT 'user'")

    logger.info("Migrated v18: Added ownership fields")


def _migrate_v19(conn) -> None:
    """v18 -> v19: 认知底层字段 (memorycandidate.role, memoryitem.role, memoryitem.promotion_trace)"""
    if not _has_column(conn, "memorycandidate", "role"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN role TEXT DEFAULT 'observation'")
    if not _has_column(conn, "memoryitem", "role"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN role TEXT DEFAULT 'observation'")
    if not _has_column(conn, "memoryitem", "promotion_trace"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN promotion_trace TEXT DEFAULT '{}'")
    logger.info("Migrated v19: Cognitive fields (role, promotion_trace)")


def _migrate_v20(conn) -> None:
    """v19 -> v20: Knowledge Lifecycle 状态机字段 (v0.4)"""
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
    except Exception as exc:
        logger.warning(
            "迁移 v20 索引创建跳过 ix_memoryitem_lifecycle_status/ix_memoryitem_superseded_by: %s",
            exc,
        )
    logger.info("Migrated v20: Knowledge Lifecycle fields")


def _migrate_v21(conn) -> None:
    """v20 -> v21: 更漏（ADR-0048）双时间轴——event_time 两新列 + valid_from 回填 + 时效三索引"""
    # 空库/无表场景（迁移测试建账用例）：表由 create_all 负责，迁移只记账不建表
    has_memoryitem = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memoryitem'"
        ).fetchone()
    )
    if has_memoryitem:
        # DDL 固定字面量（SQLite ALTER TABLE 不支持绑定参数），_has_column 幂等守卫
        if not _has_column(conn, "memoryitem", "event_time"):
            conn.execute("ALTER TABLE memoryitem ADD COLUMN event_time DATETIME")
        if not _has_column(conn, "memoryitem", "event_time_precision"):
            conn.execute("ALTER TABLE memoryitem ADD COLUMN event_time_precision TEXT DEFAULT ''")
        # 回填（spec §5.3）：valid_from 语义必填，存量 NULL 以 created_at 近似——
        # 当前态下 created_at ≤ now 恒真，行为与 NULL 等价零回归；as-of 判定
        # 「any-of + unknown 软放行」使回填无法制造新的误排除。DML 常量字面量，无注入面。
        # 双列守卫：极老库（缺 valid_from/created_at 任一列）跳过回填，宁欠不炸。
        if _has_column(conn, "memoryitem", "valid_from") and _has_column(
            conn, "memoryitem", "created_at"
        ):
            conn.execute("UPDATE memoryitem SET valid_from = created_at WHERE valid_from IS NULL")
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
            except Exception as exc:
                logger.warning("迁移 v21 索引创建跳过 event_time/valid_from/valid_to: %s", exc)
    logger.info("Migrated v21: Genglou bi-temporal event time (ADR-0048)")


def _migrate_v22(conn) -> None:
    """v21 -> v22: 回执链一等化（ADR-0049）——request_id + receipt_status + receipt_at"""
    has_re = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_event'"
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
        except Exception as exc:
            logger.warning(
                "迁移 v22 索引创建跳过 ix_retrieval_event_request_id/"
                "ix_retrieval_event_receipt_status: %s",
                exc,
            )
    logger.info("Migrated v22: receipt chain (ADR-0049)")


def _migrate_v23(conn) -> None:
    """v22 -> v23: 沉潜过审（ADR-0050）——consolidation_run 运行留痕表"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS consolidation_run ("
        "id TEXT PRIMARY KEY, ran_at DATETIME, "
        "mode TEXT DEFAULT 'off', "
        "clusters INTEGER DEFAULT 0, purified_ok INTEGER DEFAULT 0, "
        "proposals_created INTEGER DEFAULT 0, "
        "skipped_dupes INTEGER DEFAULT 0, "
        "skipped_rejected_cooldown INTEGER DEFAULT 0, "
        "skipped_lowq INTEGER DEFAULT 0, "
        "pruned INTEGER DEFAULT 0, error TEXT DEFAULT '')"
    )
    logger.info("Migrated v23: consolidation audit gate run ledger (ADR-0050)")


def _migrate_v24(conn) -> None:
    """v23 -> v24: 提案裁决时刻列（ADR-0053）——decided_at"""
    has_mp = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memoryproposal'"
        ).fetchone()
    )
    if has_mp and not _has_column(conn, "memoryproposal", "decided_at"):
        conn.execute("ALTER TABLE memoryproposal ADD COLUMN decided_at DATETIME")
    logger.info("Migrated v24: proposal decided_at (ADR-0053)")


def _migrate_v25(conn) -> None:
    """v24 -> v25: 核心记忆块补归属列（票 .scratch/ownership-gaps/04）"""
    has_cmb = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'corememoryblock'"
        ).fetchone()
    )
    if has_cmb:
        if not _has_column(conn, "corememoryblock", "tenant_id"):
            conn.execute("ALTER TABLE corememoryblock ADD COLUMN tenant_id TEXT")
        if not _has_column(conn, "corememoryblock", "user_id"):
            conn.execute("ALTER TABLE corememoryblock ADD COLUMN user_id TEXT")
        if not _has_column(conn, "corememoryblock", "agent_id"):
            conn.execute("ALTER TABLE corememoryblock ADD COLUMN agent_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_corememoryblock_owner "
                "ON corememoryblock (user_id, namespace)"
            )
        except Exception as exc:  # 索引失败不阻断启动，但必须留痕
            logger.warning("迁移跳过 ix_corememoryblock_owner: %s", exc)
    logger.info("Migrated v25: core memory block ownership (ownership-gaps/04)")


def _migrate_v26(conn) -> None:
    """v25 -> v26: 记忆分类树节点补归属列（票 .scratch/readside-gaps/08）"""
    has_mn = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memorynode'"
        ).fetchone()
    )
    if has_mn:
        if not _has_column(conn, "memorynode", "tenant_id"):
            conn.execute("ALTER TABLE memorynode ADD COLUMN tenant_id TEXT")
        if not _has_column(conn, "memorynode", "user_id"):
            conn.execute("ALTER TABLE memorynode ADD COLUMN user_id TEXT")
        if not _has_column(conn, "memorynode", "agent_id"):
            conn.execute("ALTER TABLE memorynode ADD COLUMN agent_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_memorynode_owner ON memorynode (user_id, namespace)"
            )
        except Exception as exc:  # 索引失败不阻断启动，但必须留痕
            logger.warning("迁移跳过 ix_memorynode_owner: %s", exc)
    logger.info("Migrated v26: tree node ownership (readside-gaps/08)")


# 迁移块表：按版本顺序执行。新增迁移时在末尾追加 `_migrate_vN` 并把
# TARGET_SCHEMA_VERSION 提到 N。
def _migrate_v27(conn) -> None:
    """v26 -> v27: memoryitem 补 reason 列(模型加入后迁移链漏写,老库列表查询 500)"""
    if not _has_column(conn, "memoryitem", "reason"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN reason TEXT DEFAULT ''")
    logger.info("Migrated v27: memoryitem.reason column")


def _migrate_v28(conn) -> None:
    """v27 -> v28: memoryedge 补 reason 列(模型声明了但迁移链漏写,老库 supersedes 查询报错)"""
    if not _has_column(conn, "memoryedge", "reason"):
        conn.execute("ALTER TABLE memoryedge ADD COLUMN reason TEXT DEFAULT ''")
    logger.info("Migrated v28: memoryedge.reason column")


def _migrate_v29(conn) -> None:
    """v28 -> v29: cognitivepattern 补 dedup_key 列（反思写库幂等去重，票 .scratch/review-fixes-2026-10/02）"""
    if not _has_column(conn, "cognitivepattern", "dedup_key"):
        conn.execute("ALTER TABLE cognitivepattern ADD COLUMN dedup_key TEXT")
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cognitivepattern'"
    ).fetchone():
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cognitivepattern_dedup_key"
                " ON cognitivepattern (dedup_key)"
            )
        except Exception as exc:  # 索引失败不阻断启动，但必须留痕（同 v17/v20 口径）
            logger.warning("迁移 v29 索引创建跳过 ix_cognitivepattern_dedup_key: %s", exc)
    logger.info("Migrated v29: cognitivepattern.dedup_key column（反思幂等去重）")


def _migrate_v30(conn) -> None:
    """v29 -> v30: llm_call_event 表（票 J：司天 24h LLM 降级率数据源）。

    全新库由 create_all 直接建表；老库走本块补建。索引建失败不阻断
    启动（同 v17/v20/v29 口径），但留痕。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_call_event ("
        "id TEXT PRIMARY KEY, channel TEXT, outcome TEXT, "
        "duration_ms INTEGER DEFAULT 0, created_at DATETIME)"
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_llm_call_event_created_at ON llm_call_event (created_at)"
        )
    except Exception as exc:
        logger.warning("迁移 v30 索引创建跳过 ix_llm_call_event_created_at: %s", exc)
    logger.info("Migrated v30: llm_call_event 表（LLM 降级率数据源）")


def _migrate_v31(conn) -> None:
    """v30 -> v31: 完璧（ADR-0057）多实体事实原子表与实体投影表。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fact_atomic ("
        "id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, agent_id TEXT, "
        "statement TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT, "
        "confidence REAL DEFAULT 0.5, valid_from TEXT, valid_to TEXT, "
        "source_memory_id TEXT, created_at DATETIME)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fact_entity ("
        "fact_id TEXT NOT NULL REFERENCES fact_atomic(id) ON DELETE CASCADE, "
        "entity_name TEXT NOT NULL, role TEXT NOT NULL, "
        "PRIMARY KEY (fact_id, entity_name, role))"
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_fact_atomic_statement ON fact_atomic (statement)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_fact_atomic_predicate ON fact_atomic (predicate)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_fact_atomic_source_memory_id ON fact_atomic (source_memory_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_fact_entity_name ON fact_entity (entity_name)")
    except Exception as exc:
        logger.warning("迁移 v31 索引创建跳过: %s", exc)
    logger.info("Migrated v31: 完璧 fact_atomic / fact_entity (ADR-0057)")


def _migrate_v32(conn) -> None:
    """v31 -> v32: retrieval_event 补齐 tenant_id, user_id, agent_id (读侧可观测与归属收拢)"""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_event'"
    ).fetchone():
        if not _has_column(conn, "retrieval_event", "tenant_id"):
            conn.execute("ALTER TABLE retrieval_event ADD COLUMN tenant_id TEXT")
        if not _has_column(conn, "retrieval_event", "user_id"):
            conn.execute("ALTER TABLE retrieval_event ADD COLUMN user_id TEXT")
        if not _has_column(conn, "retrieval_event", "agent_id"):
            conn.execute("ALTER TABLE retrieval_event ADD COLUMN agent_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_retrieval_event_user_id ON retrieval_event (user_id)"
            )
        except Exception as exc:
            logger.warning("迁移 v32 索引创建跳过: %s", exc)
    logger.info("Migrated v32: retrieval_event 补齐身份归属字段")


def _migrate_v33(conn) -> None:
    """v32 -> v33: memoryitem 补齐 role 列（认知角色分类）。"""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memoryitem'"
    ).fetchone():
        if not _has_column(conn, "memoryitem", "role"):
            conn.execute("ALTER TABLE memoryitem ADD COLUMN role TEXT DEFAULT 'observation'")
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS ix_memoryitem_role ON memoryitem (role)")
        except Exception as exc:
            logger.warning("迁移 v33 索引创建跳过: %s", exc)
    logger.info("Migrated v33: memoryitem 补齐 role 字段与索引")


def _migrate_v34(conn) -> None:
    """v33 -> v34: retrieval_event 补齐 session_id 列（会话检索来源链对账）。"""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_event'"
    ).fetchone():
        if not _has_column(conn, "retrieval_event", "session_id"):
            conn.execute("ALTER TABLE retrieval_event ADD COLUMN session_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_retrieval_event_session_id ON retrieval_event (session_id)"
            )
        except Exception as exc:
            logger.warning("迁移 v34 索引创建跳过: %s", exc)
    logger.info("Migrated v34: retrieval_event 补齐 session_id 字段与索引")


def _migrate_v35(conn) -> None:
    """v34 -> v35: memoryitem 补齐 shared_scope 列，skillcrystal 补齐 shared_scope / success_count / source_agent_id（同袍 ADR-0058：多智能体记忆联邦与公共知识殿堂）。"""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memoryitem'"
    ).fetchone():
        if not _has_column(conn, "memoryitem", "shared_scope"):
            conn.execute("ALTER TABLE memoryitem ADD COLUMN shared_scope TEXT DEFAULT 'private'")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_memoryitem_shared_scope ON memoryitem (shared_scope)"
            )
        except Exception as exc:
            logger.warning("迁移 v35 索引创建跳过: %s", exc)

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='skillcrystal'"
    ).fetchone():
        if not _has_column(conn, "skillcrystal", "shared_scope"):
            conn.execute("ALTER TABLE skillcrystal ADD COLUMN shared_scope TEXT DEFAULT 'private'")
        if not _has_column(conn, "skillcrystal", "success_count"):
            conn.execute("ALTER TABLE skillcrystal ADD COLUMN success_count INTEGER DEFAULT 0")
        if not _has_column(conn, "skillcrystal", "source_agent_id"):
            conn.execute("ALTER TABLE skillcrystal ADD COLUMN source_agent_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_skillcrystal_shared_scope ON skillcrystal (shared_scope)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_skillcrystal_source_agent_id ON skillcrystal (source_agent_id)"
            )
        except Exception as exc:
            logger.warning("迁移 v35 skillcrystal 索引创建跳过: %s", exc)
    logger.info(
        "Migrated v35: memoryitem 与 skillcrystal 补齐 shared_scope 等字段与索引（同袍记忆联邦）"
    )


def _migrate_v36(conn) -> None:
    """v35 -> v36: memoryitem / skillcrystal / memorycandidate 的 shared_scope 兜底补齐。

    票 05 续9（D-13/R-4）实测：v35 只给 memoryitem 与 skillcrystal 补了
    shared_scope，漏了 memorycandidate。老库迁到 user_version=35 后整列不存在，
    任何候选写入直接 `sqlite3.OperationalError: table memorycandidate has no
    column named shared_scope`；而发布门禁的 schema 探针只验 create_all 全新库，
    看不见这类漂移，于是长期假绿。

    这一跳把三张表的 shared_scope 统一兜底（幂等：已存在即跳过），顺带把
    memoryitem / skillcrystal 的列也再验一遍——万一有库在 v35 那跳中途失败过、
    version 却已被推进，这里能自愈。
    """
    for table in ("memoryitem", "skillcrystal", "memorycandidate"):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        if not _has_column(conn, table, "shared_scope"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN shared_scope TEXT DEFAULT 'private'")
        try:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_shared_scope ON {table} (shared_scope)"
            )
        except Exception as exc:
            logger.warning("迁移 v36 %s.shared_scope 索引创建跳过: %s", table, exc)
    logger.info("Migrated v36: 三张表 shared_scope 兜底补齐（修 v35 漏掉 memorycandidate）")


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _migrate_v37(conn) -> None:
    """v36 -> v37: memoryitem 补 injection_count，并把 v0.66 灌进 use_count 的注入次数挪回去。

    巡检 2026-11-03 P0（改法①）：v0.66（`93e73ac7`）让宿主注入回执给记忆
    `use_count += 1`，而 Hermes 从不发「有用」反馈——考功（use_count>=3 且采纳率
    <=0.2 → 重要度写成 0.1）与三省（use_count>=5 且 helpful=0）于是把天天在用的
    记忆判成「高频低效」。改后注入只记 `injection_count`，`use_count` 回到
    「显式反馈次数」的原义（`reflector.record_feedback` 是它唯一的写入点）。

    老库数据修复：`record_feedback` 每加一次 use_count 必写一行
    `memoryusagefeedback`（仓内没有删这张表的代码），所以
    `use_count - 反馈行数` 就是 v0.66 回执灌进来的注入次数。只处理
    `use_count > 反馈行数` 的行，把差额挪进 `injection_count`、use_count 收回到
    反馈行数；两者之和不变，遗忘的保活强度也就不变（见 `forgetting._usage_signal`）。
    反馈表不存在时无从分辨，宁 miss 不脏写：只补列不改数。幂等：修完后差额为 0。
    """
    if not _table_exists(conn, "memoryitem"):
        return
    if not _has_column(conn, "memoryitem", "injection_count"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN injection_count INTEGER NOT NULL DEFAULT 0")
    if _table_exists(conn, "memoryusagefeedback"):
        cur = conn.execute(
            "UPDATE memoryitem SET"
            " injection_count = COALESCE(injection_count, 0) + (COALESCE(use_count, 0) - ("
            "   SELECT COUNT(*) FROM memoryusagefeedback f WHERE f.memory_id = memoryitem.id)),"
            " use_count = ("
            "   SELECT COUNT(*) FROM memoryusagefeedback f WHERE f.memory_id = memoryitem.id)"
            " WHERE COALESCE(use_count, 0) > ("
            "   SELECT COUNT(*) FROM memoryusagefeedback f WHERE f.memory_id = memoryitem.id)"
        )
        moved = cur.rowcount if cur.rowcount is not None else -1
    else:
        moved = 0
        logger.info("迁移 v37：无 memoryusagefeedback 表，只补 injection_count 列、不改 use_count")
    logger.info(
        "Migrated v37: memoryitem.injection_count（注入次数与显式反馈分账），修正 %s 行", moved
    )


def _migrate_v38(conn) -> None:
    """v37 -> v38: corememoryblock (block, namespace, user_id) 唯一约束（票 23）。

    并发首写曾按「SELECT 判空 → INSERT」双插平行行（红测 F5a）。建索引前先
    去重：同 (block, namespace, user_id) 保留 version 最高（并列取 id 最大，
    稳定确定）的一行——幽灵行从未被 .first() 口径稳定寻址，留新不留旧；
    user_id IS NULL 的老行不参与去重（SQLite UNIQUE 对 NULL 视为互异，老库
    多行 NULL 属主不受影响）。老 schema 无此表时静默早退（同 v37 守卫：
    表由模型 create_all 建出，届时 __table_args__ 的唯一约束随表自带）。
    """
    if not _table_exists(conn, "corememoryblock"):
        return
    try:
        cur = conn.execute(
            """
            DELETE FROM corememoryblock
            WHERE user_id IS NOT NULL
              AND id NOT IN (
                SELECT id FROM (
                  SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY block, namespace, user_id
                    ORDER BY version DESC, id DESC
                  ) AS rn
                  FROM corememoryblock
                  WHERE user_id IS NOT NULL
                )
                WHERE rn = 1
              )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_corememoryblock_block_ns_user
            ON corememoryblock (block, namespace, user_id)
            """
        )
        logger.info("Migrated v38: corememoryblock 唯一约束（票 23），去重 %s 行", cur.rowcount)
    except Exception as exc:
        logger.warning("迁移 v38 corememoryblock 唯一索引创建跳过: %s", exc)


def _migrate_v39(conn) -> None:
    """v38 -> v39: memoryitem (memory_type, key, user_id) 部分唯一索引（票 24）。

    /memory/add/raw 与 mem create_skill 的去重是「SELECT(key=sha256) → embed
    （网络往返）→ INSERT」，MemoryItem.key 无唯一约束兜底——并发同文两路都
    判重落空即双行 verbatim + 双份向量/FTS。索引口径与去重 SELECT 逐字对齐
    （`WHERE key != '' AND status = 'active'`）：default="" 的无键行、NULL 属主
    老行（真实库 391 条 verbatim 全 NULL，SQLite 复合键含 NULL 互异）、归档/
    撤回行都不受约束。

    存量对账（票 23 v38 范式）：建索引前先去重——同 (memory_type, key,
    user_id) 的 active 非空键组**留最早一行**（ORDER BY created_at ASC,
    id ASC）：同键行内容逐字节相同（key 即正文 sha256），最早行是持有最长
    引用史的原件，竞态败行是后来者、依赖最少——与 v38「留新」相反是有意
    的（那边 .first() 寻址不稳、留新保最新内容；这边留旧保引用完整）。
    NULL 属主同键组不参与去重（宁 miss 不脏写：无法判定属主归属，删错
    即丢数据），只清点留痕，由写入路径的 SELECT 去重继续兜底。
    老库缺列时静默早退（同 v37/v38 守卫：列由模型 create_all 建出，届时
    __table_args__ 的部分唯一索引随表自带）。
    """
    if not _table_exists(conn, "memoryitem"):
        return
    if not _has_column(conn, "memoryitem", "key") or not _has_column(conn, "memoryitem", "status"):
        return
    guard = "key IS NOT NULL AND key != '' AND status = 'active' AND user_id IS NOT NULL"
    cur = conn.execute(
        f"""
        DELETE FROM memoryitem
        WHERE {guard}
          AND id NOT IN (
            SELECT id FROM (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY memory_type, key, user_id
                ORDER BY created_at ASC, id ASC
              ) AS rn
              FROM memoryitem
              WHERE {guard}
            )
            WHERE rn = 1
          )
        """
    )
    removed = cur.rowcount if cur.rowcount is not None else -1
    null_groups = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT memory_type, key FROM memoryitem
          WHERE key IS NOT NULL AND key != '' AND status = 'active' AND user_id IS NULL
          GROUP BY memory_type, key HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_memoryitem_type_key_user_active
            ON memoryitem (memory_type, key, user_id)
            WHERE key != '' AND status = 'active'
            """
        )
        logger.info(
            "Migrated v39: memoryitem 内容键部分唯一索引（票 24），去重 %s 行；"
            "NULL 属主同键组 %s 组保留（不脏写，SELECT 去重兜底）",
            removed,
            null_groups,
        )
    except Exception as exc:
        logger.warning("迁移 v39 memoryitem 唯一索引创建跳过: %s", exc)


def _migrate_v40(conn) -> None:
    """v39 -> v40: 模型索引全量同步（幂等，单一真源 = SQLModel.metadata，票 25）。

    病灶：索引只有两条出生路径——表首次创建时 create_all 随表落索引（SQLAlchemy
    对已存在的表整体跳过，索引不补）、迁移链手写 CREATE INDEX。升级库
    （remembrance.db 形态：身份时代之前的存量库）经迁移链 ALTER 补列后，身份列
    以无索引形态进入每条检索的 WHERE 过滤，且无任何机制日后补上——模型声明
    156 个索引、迁移链手写仅覆盖 27 组（立案探针 `.scratch/_v068_probe_r5_index_gap.py`
    实跑 147/27/122，票 23/24 又各补一枚部分唯一索引）。

    同步循环：遍历模型声明的全部索引，表存在才 CREATE INDEX；缺失补建
    （logger.info 留痕——大表首次建索引持写锁，运维须可见首启变慢原因）、
    已有跳过；单索引失败 warning 留痕不阻断链（同 v36 块内隔离口径，失败
    索引下次启动重试）。此后迁移链不再手写新索引，索引增减只改模型。
    边界如实声明：迁移链手写的 8 个链外索引名（含票 23 v38 的
    uq_corememoryblock_block_ns_user 唯一索引——模型侧是 UniqueConstraint，
    落 sqlite_autoindex，与本显式索引并存）保持原样不清退，除重属后续票。
    """
    # 惰性导入：本模块头注释明示模块级只依赖 logger（单向依赖铁律）；
    # tables.py / trust_models 只依赖 sqlmodel 与枚举，运行期成环风险为零。
    from sqlalchemy.dialects import sqlite as sa_sqlite
    from sqlalchemy.schema import CreateIndex
    from sqlmodel import SQLModel

    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.parameters.trust_models  # noqa: F401  参数信任面表同批注册

    dialect = sa_sqlite.dialect()
    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    total = created = failed = skipped_cols = 0
    for table in sorted(SQLModel.metadata.tables.values(), key=lambda t: t.name):
        if not _table_exists(conn, table.name):
            continue
        table_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table.name})").fetchall()}
        for ix in sorted(table.indexes, key=lambda i: i.name):
            total += 1
            if ix.name in existing:
                continue
            missing_cols = [c.name for c in ix.columns if c.name not in table_cols]
            if missing_cols:
                # 列维度的既有缺口（部分被索引列链上无对应 ADD COLUMN，如
                # skillcrystal 身份列）——不是本同步步能修的，越权补列违反
                # 「列迁移归 legacy_parity 对账」的分工。INFO 留痕不告警：
                # 老库前向迁移必须安静（TestIndexCreationFailureIsLogged 契约），
                # 真失败（同名冲突等）才走下方 warning 隔离路径。
                skipped_cols += 1
                logger.info(
                    "迁移 v40 索引 %s 跳过（列缺失，列维度归 legacy_parity 对账）表 %s 缺 %s",
                    ix.name,
                    table.name,
                    ",".join(missing_cols),
                )
                continue
            try:
                conn.execute(str(CreateIndex(ix).compile(dialect=dialect)))
                existing.add(ix.name)
                created += 1
                logger.info(
                    "迁移 v40 补建索引 %s（表 %s）",
                    ix.name,
                    table.name,
                )
            except Exception as exc:
                failed += 1
                logger.warning("迁移 v40 索引 %s 建立跳过: %s", ix.name, exc)
    logger.info(
        "Migrated v40: 模型索引同步（票 25）：声明 %s 个，补建 %s，"
        "列缺失跳过 %s（列维度归 legacy_parity），失败隔离 %s",
        total,
        created,
        skipped_cols,
        failed,
    )


def _migrate_v41(conn) -> None:
    """v40 -> v41（票 49 观测面）：retrieval_event 补 fts_terms_truncated。

    FTS 关键词超 _GRAM_TERMS_MAX 的截断事实落列（is_system_noise 同款
    BOOLEAN DEFAULT 0 先例）；索引同步：v40 的模型索引全量同步在旧库上会
    因列未存在而跳过本列索引（v40 先于 v41），此处显式补建。幂等。
    """
    if not _has_column(conn, "retrieval_event", "fts_terms_truncated"):
        conn.execute("ALTER TABLE retrieval_event ADD COLUMN fts_terms_truncated BOOLEAN DEFAULT 0")
    # 建表归 create_all（_has_column 同款口径）——表不存在的库（迁移链夹具等）
    # 跳过索引补建，否则本块炸链、版本停在 v40
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_event'"
    ).fetchone()
    if has_table:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_retrieval_event_fts_terms_truncated"
            " ON retrieval_event (fts_terms_truncated)"
        )
    logger.info("数据库增量迁移 v41 完成（FTS 截断观测列）")


def _migrate_v42(conn) -> None:
    """v41 -> v42（票 68）：cognitivepattern.dedup_key 升唯一索引。

    应用层 check-then-act（先查后插）在并发下可双行落库，schema 层无兜底。
    存量库先按 dedup_key 去重（同键保留最早 rowid——模式行是派生数据、同签名
    语义等价，最早行观察窗最完整），再重建唯一索引；v29 落的旧同名索引是
    非唯一的，必须先 DROP 才能升唯一。索引沿用 v29 名（ix_cognitivepattern_
    dedup_key）——模型侧 index=True + unique=True 声明的同名索引在 create_all
    新库上本就是唯一的，同名同唯一性迁移库与 create_all 库才对得齐（v40 索引
    同步对账口径）。dedup_key NULL 行（v29 前存量）不参与唯一性（SQLite NULL
    互不冲突），不追溯。幂等。建表归 create_all（v41 同款口径）。
    表名为 SQLModel 默认小写 cognitivepattern（v29 同名，非下划线式）。
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cognitivepattern'"
    ).fetchone()
    if not has_table:
        return
    conn.execute(
        "DELETE FROM cognitivepattern"
        " WHERE dedup_key IS NOT NULL"
        "   AND rowid NOT IN ("
        "       SELECT MIN(rowid) FROM cognitivepattern"
        "       WHERE dedup_key IS NOT NULL GROUP BY dedup_key)"
    )
    conn.execute("DROP INDEX IF EXISTS ix_cognitivepattern_dedup_key")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_cognitivepattern_dedup_key"
        " ON cognitivepattern (dedup_key)"
    )
    logger.info("数据库增量迁移 v42 完成（dedup_key 唯一索引）")


MIGRATIONS = [
    (2, _migrate_v2),
    (3, _migrate_v3),
    (4, _migrate_v4),
    (5, _migrate_v5),
    (6, _migrate_v6),
    (7, _migrate_v7),
    (8, _migrate_v8),
    (9, _migrate_v9),
    (10, _migrate_v10),
    (11, _migrate_v11),
    (12, _migrate_v12),
    (13, _migrate_v13),
    (14, _migrate_v14),
    (15, _migrate_v15),
    (16, _migrate_v16),
    (17, _migrate_v17),
    (18, _migrate_v18),
    (19, _migrate_v19),
    (20, _migrate_v20),
    (21, _migrate_v21),
    (22, _migrate_v22),
    (23, _migrate_v23),
    (24, _migrate_v24),
    (25, _migrate_v25),
    (26, _migrate_v26),
    (27, _migrate_v27),
    (28, _migrate_v28),
    (29, _migrate_v29),
    (30, _migrate_v30),
    (31, _migrate_v31),
    (32, _migrate_v32),
    (33, _migrate_v33),
    (34, _migrate_v34),
    (35, _migrate_v35),
    (36, _migrate_v36),
    (37, _migrate_v37),
    (38, _migrate_v38),
    (39, _migrate_v39),
    (40, _migrate_v40),
    (41, _migrate_v41),
    (42, _migrate_v42),
]


def apply_migrations(conn) -> None:
    """基于 user_version 的增量迁移链（无损升级，幂等，异常不阻断启动）。

    票 `.scratch/migration-chain/01`：原来是 25 个线性 `if user_version < N`
    块，任何一步抛异常就跳出整个函数，`PRAGMA user_version = N` 写在块尾，
    于是 version 停在失败那一跳、后续全部跳过，而外层只 logger.error 一行、
    服务照常启动——坏了半个月没人知道。

    现在逐块 try/except：一块失败不影响后面的块，失败的跳记下来由
    `/health/deep` 报出。**仍然不阻断启动**（fts-availability/01 的判断标准：
    一个非关键索引建不起来不该让整个服务起不来）。

    语义：`_MIGRATION_FAILURES` 记录的是**本次调用**的失败，不是历史累积——
    入口处清空。否则同一进程里第二次调用会把上一次的失败也报出来
    （健康检查于是永远红，且测试之间互相污染）。
    """
    _MIGRATION_FAILURES.clear()
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    # 未版本化库（全新库或老库）基线置为 v1
    if user_version == 0:
        user_version = 1
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    for target, fn in MIGRATIONS:
        if user_version >= target:
            continue
        try:
            fn(conn)
        except Exception as exc:
            # 逐块隔离的核心：记下来、继续跑后面的块。version 不推进，
            # 下次启动会重试这一跳（瞬时失败自愈，持久失败持续可见）。
            logger.error("迁移 v%d 失败（后续迁移继续）: %s", target, exc)
            _MIGRATION_FAILURES.append({"version": target, "error": str(exc)})
            continue
        # 只在块真的成功后推进版本——否则下次启动会跳过这一跳。
        # target 是 MIGRATIONS 表里的 int 字面量，不是外部输入；
        # PRAGMA 不支持绑定参数，故用 f-string 而非 % 格式化。
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
        # 局部快照必须跟着走：否则循环仍用初始值判断，后续块会被误跳过
        # （等价性验证抓到的真实 bug——表和索引都对，version 却停在 23）。
        user_version = target
