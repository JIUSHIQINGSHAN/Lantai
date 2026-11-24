"""
数据库初始化与 FTS5 全文搜索
"""

from sqlmodel import Session, SQLModel, create_engine

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.storage.fts import init_fts
from lantai.storage.migrations import (
    TARGET_SCHEMA_VERSION,
    _has_column,
    apply_migrations,
    get_migration_failures,
)

# `_has_column` 随迁移拆分移到了 `storage/migrations.py`（它是 25 个迁移块
# 共用的 helper）。这里再导出一次：`migrations_v022.py` 与
# `tests/test_ownership_migration.py` 都从 db 导入它，改导入路径会让它们
# 静默失效——「宁 miss 不脏写」对重构同样成立，兼容导入路径是低成本保险。
# 完整 `__all__` 见文件末尾（那里所有名字都已定义）。

# busy_timeout=30s：worker/调度器/请求线程多写面并发时避免瞬时
# "database is locked" 静默丢数据（票 77：原注释理由引用已删除的死接线模块
# 「压缩前抢救 daemon 线程并发写库」，随删除案改写——参数本体对其余并发写
# 路径仍有价值，删理由不删参数）
engine = create_engine(settings.DATABASE_URL, echo=False, connect_args={"timeout": 30})

# ── Schema 版本化（v0.6 Ticket 01，借鉴 aiduMEI v18.3 Fast-Update）──
# PRAGMA user_version 记录数据库结构版本；未版本化库（全新库或 v0.5 及以前
# 老库）自动基线为 v1，增量补丁按版本号依次执行。ALTER TABLE ADD COLUMN 为
# 毫秒级操作，代码更新与数据重构解耦，异常只记日志不阻断启动（降级而非崩溃）。
# 目标版本号见下方 `CURRENT_SCHEMA_VERSION`（真源在 storage/migrations.py，
# 这里原来另有一份硬编码的 26——两处真相，改版本时漏改哪边都不报错）。

# FTS5 词汇召回通道是否可用（票 .scratch/fts-availability/01）：init_db 内由
# init_fts 的返回值置位。默认 None = 尚未初始化（测试进程里 init_db 未被调用时
# 就是 None，此时不得当作「不可用」——无法区分「没查过」与「查了是坏的」）。
FTS_OK: bool | None = None


# 迁移链已拆到 `storage/migrations.py`（票 `.scratch/migration-chain/01`）：
# 原来是 25 个线性 `if user_version < N` 块，任何一步抛异常就跳出整个函数，
# version 停在失败那一跳、后续全部静默跳过，而外层只 logger.error 一行、
# 服务照常启动。现逐块隔离 + 失败留痕 + `/health/deep` 可见。
# `TARGET_SCHEMA_VERSION` / `get_migration_failures` / `apply_migrations`
# 与 25 个 `_migrate_vN` 都在那个模块，在上面导入后从这里再导出，
# 以保持既有 `from lantai.storage.db import apply_migrations` 路径可用。

# Schema 目标版本。迁移块各自维护，这里是给调用方读的单一真源
# （原来 db.py 自己硬编码一份 26，与迁移链里的 `if user_version < N`
#  是两处真相——改版本时漏改哪边都不报错）。
CURRENT_SCHEMA_VERSION = TARGET_SCHEMA_VERSION

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "TARGET_SCHEMA_VERSION",
    "_has_column",
    "apply_migrations",
    "get_migration_failures",
    "engine",
    "init_db",
    "get_session",
    "get_session_dep",
]


def init_db():
    from lantai.models import tables  # noqa

    SQLModel.metadata.create_all(engine)
    # 幂等列迁移：老库缺列时 create_all 不会加列，统一走 user_version 增量链
    conn = None
    try:
        conn = engine.raw_connection()
        apply_migrations(conn)
    finally:
        if conn is not None:
            conn.close()

    # 初始化 FTS5（词汇召回通道）。返回 False 时**不抛**——抛出去会让整个服务
    # 起不来，而这是「降级但可用」状态（SQLite + 向量召回仍工作）。改为如实留痕，
    # 并由 /health/deep 的 checks["fts"] 让运维看得见（票 .scratch/fts-availability/01）。
    # 连接必须归还：`init_fts` 只在成功路径 commit、从不到 close（fts.py），
    # 修前这里漏关，启动即漏一条 DBAPI 连接直到 GC——与上方迁移块的正确写法
    # （try/finally close）不一致。回归锁 tests/test_opt_refactor_smoke.py。
    conn = engine.raw_connection()
    global FTS_OK
    try:
        FTS_OK = init_fts(conn)
    finally:
        conn.close()
    if not FTS_OK:
        logger.warning(
            "FTS5 词汇召回通道不可用（详见上方 error 日志）：检索将退回向量通道，"
            "中文子串匹配可能全部失效；/health/deep 的 checks.fts 会报告 fail"
        )


def get_session() -> Session:
    return Session(engine)


def get_session_dep():
    """FastAPI 依赖专用会话（生成器形态 ⇒ 请求结束一定 close）。

    `get_session()` 返回裸 `Session`，被 `Depends` 用作普通函数依赖时 FastAPI
    **不做 teardown**——只有生成器依赖才会在请求结束时执行 `finally`。此前
    `routes_cognitive` 的五个端点（observe / reflect / context / context/prompt）
    整个请求期间持有连接池连接，且异常路径上没有确定性 `rollback`，连接要等
    对象被 GC 才归还（`CognitiveContextBuilder` 持有 db、大结果对象有引用环时
    会拖延）。`get_session()` 保留给 341 处 `with` 用法——生成器不能 `with`。
    """
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
