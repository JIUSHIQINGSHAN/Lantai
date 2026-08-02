"""
数据库初始化与 FTS5 全文搜索
"""
import sqlite3
from sqlmodel import SQLModel, Session, create_engine
from remembrance.core.settings import settings
from remembrance.storage.fts import init_fts

engine = create_engine(settings.DATABASE_URL, echo=False)

def init_db():
    from remembrance.models import tables  # noqa
    SQLModel.metadata.create_all(engine)
    # 初始化 FTS5
    conn = engine.raw_connection()
    init_fts(conn)

def get_session() -> Session:
    return Session(engine)
