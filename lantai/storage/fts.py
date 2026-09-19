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
    conn.execute(text("DELETE FROM memory_fts WHERE memory_id = :id"), {"id": memory_id})
    if content:
        conn.execute(
            text("INSERT INTO memory_fts(memory_id, content) VALUES (:id, :content)"),
            {"id": memory_id, "content": content},
        )


def index_fts(conn: sqlite3.Connection, memory_id: str, content: str):
    """索引单条（独立连接场景；生产路径用 sync_fts，此函数仅供测试/脚本）。"""
    try:
        conn.execute(
            "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)", (memory_id, content)
        )
        conn.commit()
    except Exception as e:
        logger.warning("FTS5 index failed for %s: %s", memory_id, e)


_GRAM_TERMS_MAX = 24


def _bm25_keywords(query: str) -> list:
    """BM25 关键词（票06）：CJK 3-gram 滑窗 + ASCII 整词，单一真源，OR/AND 两路共用。

    trigram 索引的最小匹配单元是 3 字符——中文 2 字词（偏好/机房间）天然不可
    匹配，整句短语匹配（旧行为）则不覆盖词中错字与词面重叠改写。3-gram 滑窗
    让错字只污染个别 gram、共享词根即可部分命中；OR + bm25 排序负责降噪。
    上限 _GRAM_TERMS_MAX 防 OR 链过长（现状整改票04 如实声明：同样作用于 AND
    路径 search_fts——超 24 gram 的长查询尾部被静默截断，存在假阳性面）。
    """
    from lantai.core.settings import settings

    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", query or "")
    if not settings.FTS_BM25_GRAM_TOKENIZE:
        return [w.strip() for w in cleaned.split() if len(w.strip()) >= 3]
    terms: list = []
    seen = set()
    for word in cleaned.split():
        if all(not ("\u4e00" <= ch <= "\u9fff") for ch in word):
            if len(word) >= 3 and word not in seen:
                seen.add(word)
                terms.append(word)
            continue
        for i in range(len(word) - 2):
            gram = word[i : i + 3]
            if gram not in seen:
                seen.add(gram)
                terms.append(gram)
    return terms[:_GRAM_TERMS_MAX]


def search_fts(
    conn, query: str, top_k: int = 5, lanes: list = None, domain: str = None, principal=None
) -> list:
    try:
        # 票06 + 现状整改票04：本路径（AND 确定性面）与 OR 召回面共用 _bm25_keywords。
        # 默认开档下关键词同为 CJK 3-gram：AND 语义是「各 gram 任意位置全命中」，
        # 非旧「整句短语连续匹配」；已知假阳性面为 gram 跨位拼合（「机器学…器学习」
        # 分置两处亦命中）。off 档退回旧空白切分整词短语匹配。
        keywords = _bm25_keywords(query)
        if not keywords:
            return []
        match_query = " AND ".join('"' + k.replace('"', '""') + '"' for k in keywords)

        sql = """
            SELECT f.memory_id 
            FROM memory_fts f
            JOIN memoryitem m ON f.memory_id = m.id
            WHERE f.content MATCH ?
        """
        params = [match_query]

        if lanes:
            sql += f" AND m.lane IN ({','.join(['?'] * len(lanes))})"
            params.extend(lanes)
        if domain and domain != "all":
            sql += " AND m.domain = ?"
            params.append(domain)
        if principal:
            if getattr(principal, "tenant_id", None):
                sql += " AND m.tenant_id = ?"
                params.append(principal.tenant_id)
            if getattr(principal, "user_id", None):
                sql += " AND m.user_id = ?"
                params.append(principal.user_id)
            if getattr(principal, "session_id", None):
                sql += " AND m.session_id = ?"
                params.append(principal.session_id)

        sql += " ORDER BY rank LIMIT ?"
        params.append(top_k)

        cursor = conn.execute(sql, tuple(params))
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("FTS search failed: %s", e)
        return []


def search_fts_bm25(
    conn, query: str, top_k: int = 50, lanes: list = None, domain: str = None, principal=None
) -> list:
    try:
        keywords = _bm25_keywords(query)
        if not keywords:
            return []
        match_query = " OR ".join('"' + k.replace('"', '""') + '"' for k in keywords)

        sql = """
            SELECT f.memory_id, bm25(memory_fts, 10.0, 5.0) as score 
            FROM memory_fts f
            JOIN memoryitem m ON f.memory_id = m.id
            WHERE f.memory_fts MATCH ?
        """
        params = [match_query]

        if lanes:
            sql += f" AND m.lane IN ({','.join(['?'] * len(lanes))})"
            params.extend(lanes)
        if domain and domain != "all":
            sql += " AND m.domain = ?"
            params.append(domain)
        if principal:
            if getattr(principal, "tenant_id", None):
                sql += " AND m.tenant_id = ?"
                params.append(principal.tenant_id)
            if getattr(principal, "user_id", None):
                sql += " AND m.user_id = ?"
                params.append(principal.user_id)
            if getattr(principal, "session_id", None):
                sql += " AND m.session_id = ?"
                params.append(principal.session_id)

        sql += " ORDER BY score LIMIT ?"
        params.append(top_k)

        cursor = conn.execute(sql, tuple(params))
        return [(row[0], row[1]) for row in cursor.fetchall()]
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("FTS BM25 search failed: %s", e)
        return []
