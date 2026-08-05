"""
数据库初始化与 FTS5 全文搜索
"""
import sqlite3
from sqlmodel import SQLModel, Session, create_engine
from remembrance.core.settings import settings
from remembrance.core.logger import logger
from remembrance.storage.fts import init_fts

# busy_timeout=30s：pre_compress daemon 线程与请求线程并发写库时避免
# 瞬时 "database is locked" 静默丢数据
engine = create_engine(settings.DATABASE_URL, echo=False,
                       connect_args={"timeout": 30})

def init_db():
    from remembrance.models import tables  # noqa
    SQLModel.metadata.create_all(engine)
    # 幂等列迁移：v0.4 新增 decay_class（老库无此列，create_all 不会加列）
    conn = None
    try:
        conn = engine.raw_connection()
        conn.execute(
            "ALTER TABLE memoryitem ADD COLUMN decay_class TEXT DEFAULT 'episodic'")
        conn.commit()
    except Exception as e:
        # 列已存在（sqlite duplicate column）属正常幂等路径；其余异常记录日志
        if "duplicate column" not in str(e).lower():
            logger.warning("decay_class migration skipped: %s", e)
    finally:
        if conn is not None:
            conn.close()
    # 初始化 FTS5
    conn = engine.raw_connection()
    init_fts(conn)

def get_session() -> Session:
    return Session(engine)
