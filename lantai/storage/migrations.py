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
TARGET_SCHEMA_VERSION = 26

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
    """v18 -> v19: 认知底层字段 (memorycandidate.role, memoryitem.promotion_trace)"""
    if not _has_column(conn, "memorycandidate", "role"):
        conn.execute("ALTER TABLE memorycandidate ADD COLUMN role TEXT DEFAULT 'observation'")
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
