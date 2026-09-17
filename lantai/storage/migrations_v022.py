"""v022 上游吸收增量迁移（票据 05）：retrieval_event 补 session_id。

独立于 `lantai/storage/db.py` 的 user_version 迁移链——那是历史基线，
本文件只承载 v022 吸收波的补充迁移，由 `api.app` 启动时在 `init_db()`
之后调用。幂等：列已存在即跳过；异常只记日志不阻断启动（降级而非崩溃）。

判据背景：写线活性用「带 session 的真实会话检索」计数，后台巡检读到的
全是自己的心跳（上游 aiduMEI 写线断裂事故教训，票据 05）。
"""

from lantai.core.logger import logger
from lantai.storage.db import _has_column


def apply_v022_migrations(engine) -> None:
    """幂等补充迁移：retrieval_event.session_id + 检索索引。

    DDL 固定字面量（SQLite ALTER TABLE 不支持绑定参数，防注入纪律）。"""
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
        conn.commit()
        logger.info("v022 迁移完成：retrieval_event.session_id（写线活性判据）")
    except Exception as exc:
        logger.error("v022 增量迁移异常（服务继续启动）: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
