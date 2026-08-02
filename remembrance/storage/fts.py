"""
FTS5 全文搜索：创建 trigram 分词器的 FTS5 虚拟表
"""
import sqlite3
from remembrance.core.logger import logger


def init_fts(conn: sqlite3.Connection):
    """初始化 FTS5 虚拟表，使用 trigram 分词器"""
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                memory_id UNINDEXED,
                content,
                tokenize='trigram'
            )
        """)
        conn.commit()
        logger.info("FTS5 + trigram initialized")
    except Exception as e:
        logger.warning("FTS5 init failed: %s", e)


def index_fts(conn: sqlite3.Connection, memory_id: str, content: str):
    """将记忆内容索引到 FTS5"""
    try:
        conn.execute(
            "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
            (memory_id, content)
        )
        conn.commit()
    except Exception as e:
        logger.warning("FTS5 index failed for %s: %s", memory_id, e)


def search_fts(conn: sqlite3.Connection, query: str, top_k: int = 5) -> list[str]:
    """FTS5 全文搜索，返回匹配的记忆 ID 列表"""
    try:
        # 将查询拆分为关键词，用 AND 连接
        keywords = [w.strip() for w in query.split() if w.strip()]
        if not keywords:
            return []
        match_query = " AND ".join(keywords)
        cursor = conn.execute(
            "SELECT memory_id FROM memory_fts WHERE content MATCH ? ORDER BY rank LIMIT ?",
            (match_query, top_k)
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("FTS5 search failed: %s", e)
        return []
