"""
FTS5 全文搜索：trigram 分词器 + 同事务写入同步（ADR-0008）

- init_fts：建表；检测到旧 schema（无 memory_id 列）自动 DROP 重建（旧表从无数据，无损失）
- sync_fts：在调用方的 SQLAlchemy 事务内同步索引（强一致）
- search_fts：子串召回
"""

import re
import sqlite3

from lantai.core.logger import logger

# 显式系统身份的 user_id（票 `.scratch/mcp-identity-gaps/06`）。本地字面量
# 而不 import `acl.SYSTEM_VIEWER`：`acl.py` 顶部 `from fastapi import ...`，
# 而本模块被 `eval/offline.py` 等只装检索依赖的环境引用，减少耦合。
# 单一真源仍是 `acl.SYSTEM_VIEWER`（值 "__system__"），此处只做等价判定。
_SYSTEM_VIEWER = "__system__"


def _is_system_viewer(principal) -> bool:
    """是否为显式系统身份（worker/scheduler 全量批处理专用）。

    同 `retrieval/hybrid.py::_is_system_viewer`：两处都需要这个判定，
    但都不值得为它引入对 `acl` 的依赖（见上方 `_SYSTEM_VIEWER` 注释）。
    """
    return (getattr(principal, "user_id", None) or "") == _SYSTEM_VIEWER


def _owner_filter_clause(principal) -> tuple[str, str] | None:
    """读侧归属 SQL 片段与参数（票 `.scratch/mcp-identity-gaps/13`）。

    返回 `(sql_fragment, param)`；`None` 表示**不加归属过滤**。

    三个调用点（`search_fts` 与 `search_fts_bm25` 各一处，形状逐字相同）
    共用这一个函数——此前两处各自手写，票 11 只改到其中一处、
    差点漏另一处，故收到这里做单一真源。

    **身份收敛**（本票修的那半边）：`principal=None` 与 `user_id=""` 都经
    `acl.viewer_of` 收敛到 `"default"`，与 DEV MODE 同值。此前两道判据
    （`if principal and ...` 与 `if getattr(principal,"user_id",None):`）
    各自漏半边：
      · `None` → 整个归属块跳过 → **全表召回别人的私有记忆**
        （票 11 只在 `hybrid_search` 入口收敛，函数本身没有；换条路径进来就漏）；
      · 空串 → `getattr(..., None)` 为假 → 同样跳过。而 `viewer_of` 对空串
        **会**收敛到 `"default"`（`acl.py:62` 的 `user_id or "default"`），
        空串身份是真实可达的：`auth.py:157` `make_principal(api_key.user_id, ...)`
        的 `api_key.user_id` 是数据库列。

    **不加过滤的两种身份**：
      · `SYSTEM_VIEWER`（票 11）：worker/scheduler 显式要全表；
      · admin（票 08）：观测/排障面一律全表，同 `_kaogong_scope` /
        `get_core_memory` / `find_duplicate_verbatim` / `build_memories_page`
        四处既有形状。此前这里漏了 admin 豁免，`user_id="api_key"` 的真形态
        （`auth.py:168`）被收窄成 `user_id=='api_key'`，真实库没这个属主
        → **关键词召回对管理员基本空白**。

    注意 `None` **不是**"不加过滤"：本仓的口径（票 07/11/12 三次确立）是
    `None` → 收敛到 `"default"`，只因为 `_is_system_viewer` 与 admin 才放全表。
    """
    if principal is None:
        # 票 06 契约（`tests/test_fts_owner_recall.py::test_none_principal_unchanged`
        # 钉住）：`principal=None` 在**本函数**的语义是「不过滤」——worker /
        # 定时反思直调它不能空转。这与 `hybrid_search` 入口把 None 收敛成
        # `default`（票 11）是**两个不同层次**的决定：入口收敛保护 MCP 调用方，
        # 函数本身保留 worker 契约。改这里须先改那条测试，属承重墙，不擅动。
        return None
    if _is_system_viewer(principal):
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None

    from lantai.core.acl import viewer_of

    # `OR IS NULL` 半边不可删（票 fts-null-owner/01、03/04/05/06/15 同一口径）：
    # 真实库 96.8% 的 memoryitem 是 NULL 属主，只写等值匹配等于单人部署下
    # 关键词召回丢掉几乎全部历史。
    return " AND (m.user_id = ? OR m.user_id IS NULL)", viewer_of(principal)


def init_fts(conn: sqlite3.Connection) -> bool:
    """初始化 FTS5 虚拟表；自动迁移旧 schema。返回词汇召回通道是否真正可用。

    「可用」的判据不能只看 CREATE 没报错——`CREATE VIRTUAL TABLE IF NOT EXISTS`
    在目标已存在时是**静默 no-op**：SQLite 不报错，哪怕已存在的是张普通表、
    或分词器不对的 FTS5 表（列名检查也照样过）。实测两种坏态：

    - 同名普通表：日志仍打 `initialized`，之后每次查询
      `no such column: memory_fts` → 词汇召回整体消失；
    - FTS5 但 tokenize≠trigram：bm25 查询照样成功，只是中文子串召回
      永久失效，**任何一层都不报错**。

    故 CREATE 之后必须回读 sqlite_master 的真实定义核验（票 .scratch/fts-availability/01）。
    核验不过只报错、不抛异常、不自动重建：抛异常会一路传到 lifespan 让整个
    服务起不来（而这本是「降级但可用」——SQLite 与向量召回仍工作），DROP 重建
    则可能误删别人的表。宁 miss 不脏写：如实报告，交人处置。
    """
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
    except Exception as e:
        # 升格为 error：这一行滚过日志后，每次查询都会各自报一次
        # `no such table: memory_fts`，而现象只是「结果变少」。
        logger.error("FTS5 init failed, lexical recall unavailable: %s", e, exc_info=True)
        return False

    # IF NOT EXISTS 跳过了类型/分词器校验，此处如实核验后才敢声称成功
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'memory_fts'").fetchone()
    ddl = (row[0] or "").lower() if row else ""
    if not row or "fts5" not in ddl or "trigram" not in ddl:
        logger.error(
            "memory_fts 存在但并非预期的 FTS5/trigram 表（实际定义 %r）；"
            "词汇召回已降级——中文子串匹配可能全部失效，需人工修复",
            (row[0] if row else None),
        )
        return False

    logger.info("FTS5 + trigram initialized")
    return True


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


def fts_has_row(session, memory_id: str) -> bool:
    """FTS 行在场核查（复原路径三层自查用；同连接面，不做任何查询匹配）。"""
    from sqlalchemy import text

    conn = session.connection()
    row = conn.execute(
        text("SELECT 1 FROM memory_fts WHERE memory_id = :id LIMIT 1"), {"id": memory_id}
    ).first()
    return row is not None


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
        if principal is not None and not _is_system_viewer(principal):
            if getattr(principal, "tenant_id", None):
                sql += " AND m.tenant_id = ?"
                params.append(principal.tenant_id)
            # 归属（票 `.scratch/mcp-identity-gaps/13`）：身份收敛 + admin 豁免
            # 都收到 `_owner_filter_clause` 单一真源，两个 FTS 函数共用。
            # 此前此处手写，票 11 只改到其中一处、差点漏另一处。
            owner = _owner_filter_clause(principal)
            if owner is not None:
                sql += owner[0]
                params.append(owner[1])
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
        if principal is not None and not _is_system_viewer(principal):
            if getattr(principal, "tenant_id", None):
                sql += " AND m.tenant_id = ?"
                params.append(principal.tenant_id)
            # 归属（票 `.scratch/mcp-identity-gaps/13`）：与 `search_fts` 共用
            # `_owner_filter_clause` 单一真源——两个函数是同一处手写的同一段，
            # 改一处漏一处等于没改（票 11 就差点只改到一处）。
            owner = _owner_filter_clause(principal)
            if owner is not None:
                sql += owner[0]
                params.append(owner[1])
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
