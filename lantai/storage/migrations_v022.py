"""v022 上游吸收增量迁移（票据 05）：retrieval_event 补 session_id。

独立于 `lantai/storage/db.py` 的 user_version 迁移链——那是历史基线，
本文件只承载 v022 吸收波的补充迁移，由 `api.app` 启动时在 `init_db()`
之后调用。幂等：列已存在即跳过；异常只记日志不阻断启动（降级而非崩溃）。

判据背景：写线活性用「带 session 的真实会话检索」计数，后台巡检读到的
全是自己的心跳（上游 aiduMEI 写线断裂事故教训，票据 05）。
"""

import contextlib

from lantai.core.logger import logger
from lantai.storage.db import _has_column


def apply_v022_migrations(engine) -> None:
    """幂等补充迁移：retrieval_event.session_id + 检索索引 + 归属列。

    DDL 固定字面量（SQLite ALTER TABLE 不支持绑定参数，防注入纪律）。

    归属列（票 .scratch/readside-gaps/11 与 10）：`prompttemplate`、
    `skillcrystal`、`source`、`retrieval_event` 原本没有归属四元组，
    对应读端点一个身份都不取、模板/技能流程/来源凭证/查询词全文可读。
    迁移只加列、不回填（老行保持 NULL，读侧靠 `OR IS NULL` 兜住——
    单人部署下判"不可见"会让功能直接消失）。

    表名**必须**与 `Model.__tablename__` 逐字一致（票 11 曾写成
    `prompt_template` / `skill_crystal`，带下划线——真实库根本没这两张表）。
    `_has_column` 对不存在的表返回 True，所以写错表名不会报错，
    只会让那次 `ALTER TABLE` 静默不执行：归属列一个都没加，
    读侧 `_prompt_scope` / `_crystal_scope` 查不存在的列直接 500。
    """
    conn = None
    try:
        conn = engine.raw_connection()
        if not _has_column(conn, "retrieval_event", "session_id"):
            conn.execute("ALTER TABLE retrieval_event ADD COLUMN session_id TEXT")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_retrieval_event_session_id "
                "ON retrieval_event (session_id)"
            )
        except Exception as exc:  # 索引失败不阻断启动
            logger.warning("迁移跳过 ix_retrieval_event_session_id: %s", exc)
        for table in ("prompttemplate", "skillcrystal", "source", "retrieval_event"):
            for col in ("tenant_id", "user_id", "agent_id"):
                if not _has_column(conn, table, col):
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        conn.commit()
        logger.info(
            "v022 迁移完成：retrieval_event.session_id（写线活性判据）"
            "+ prompttemplate/skillcrystal/source/retrieval_event 归属列（票 11/10）"
        )
    except Exception as exc:
        logger.error("v022 增量迁移异常（服务继续启动）: %s", exc)
    finally:
        if conn is not None:
            # 关闭失败不阻断启动（外层已捕获异常并记日志，此处只保证不二次抛错）
            with contextlib.suppress(Exception):
                conn.close()
