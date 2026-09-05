"""
FTS5 全文搜索：trigram 分词器 + 同事务写入同步（ADR-0008）

- init_fts：建表；检测到旧 schema（无 memory_id 列）自动 DROP 重建（旧表从无数据，无损失）
- sync_fts：在调用方的 SQLAlchemy 事务内同步索引（强一致）
- search_fts：子串召回
"""
import re
import sqlite3

from lantai.core.logger import logger


def init_fts(conn: sqlite3.Connection):
    """初始化 FTS5 虚拟表；自动迁移旧 schema。"""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_fts)").fetchall()]
        if cols and "memory_id" not in cols:
            conn.execute("DROP TABLE memory_fts")
            logger.warning("legacy memory_fts schema detected, dropped for recreation")
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


def sync_fts(session, memory_id: str, content: str | None) -> None:
    """同事务同步 FTS 索引（ADR-0008：强一致，不吞异常）。

    content 非 None：先删后插（UPSERT 语义）；
    content 为 None：删除该记忆的 FTS 行。
    """
    from sqlalchemy import text
    conn = session.connection()
    conn.execute(text("DELETE FROM memory_fts WHERE memory_id = :id"),
                 {"id": memory_id})
    if content:
        conn.execute(
            text("INSERT INTO memory_fts(memory_id, content) VALUES (:id, :content)"),
            {"id": memory_id, "content": content})


def index_fts(conn: sqlite3.Connection, memory_id: str, content: str):
    """索引单条（独立连接场景；生产路径用 sync_fts，此函数仅供测试/脚本）。"""
    try:
        conn.execute(
            "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
            (memory_id, content)
        )
        conn.commit()
    except Exception as e:
        logger.warning("FTS5 index failed for %s: %s", memory_id, e)


def search_fts(conn: sqlite3.Connection, query: str, top_k: int = 5) -> list[str]:
    """FTS5 全文匹配的记忆 ID 列表"""
    try:
        query = re.sub(r'[^\w\u4e00-\u9fa5]+', ' ', query)
        keywords = [w.strip() for w in query.split() if w.strip()]
        # trigram 最小 3 字符构成，2 个汉字/ 1 字符等自动无法生成词。
        # 自动无法生成词，AND 且查不到，导致 "API 接口" 查不到接口。
        # 将过短词剔除，交由兜底处理。
        keywords = [k for k in keywords if len(k) >= 3]
        if not keywords:
            return []
        match_query = " AND ".join(
            '"' + k.replace('"', '""') + '"' for k in keywords)
        cursor = conn.execute(
            "SELECT memory_id FROM memory_fts WHERE content MATCH ? ORDER BY rank LIMIT ?",
            (match_query, top_k)
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("FTS5 search failed: %s", e)
        return []

def search_fts_bm25(conn: sqlite3.Connection, query: str, top_k: int = 50) -> list[tuple[str, float]]:
    """FTS5 全文匹配 (记忆ID, bm25分) 列表（越小越好，通常为负）"""
    try:
        query = re.sub(r'[^\w\u4e00-\u9fa5]+', ' ', query)
        keywords = [w.strip() for w in query.split() if w.strip()]
        keywords = [k for k in keywords if len(k) >= 3]
        if not keywords:
            return []
        match_query = " OR ".join(
            '"' + k.replace('"', '""') + '"' for k in keywords)
        
        # 使用 sqlite 的 bm25 函数
        cursor = conn.execute(
            "SELECT memory_id, bm25(memory_fts, 10.0, 5.0) as score "
            "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY score LIMIT ?",
            (match_query, top_k)
        )
        return cursor.fetchall()
    except Exception as e:
        logger.warning("FTS5 bm25 search failed: %s", e)
        return []

