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

# busy_timeout=30s：pre_compress daemon 线程与请求线程并发写库时避免
# 瞬时 "database is locked" 静默丢数据
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
    conn = engine.raw_connection()
    global FTS_OK
    FTS_OK = init_fts(conn)
    if not FTS_OK:
        logger.warning(
            "FTS5 词汇召回通道不可用（详见上方 error 日志）：检索将退回向量通道，"
            "中文子串匹配可能全部失效；/health/deep 的 checks.fts 会报告 fail"
        )


def get_session() -> Session:
    return Session(engine)
