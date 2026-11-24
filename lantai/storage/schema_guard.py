"""缺表自愈（schema drift）的唯一真源。

真实库可能滞后于代码 schema（2026-11-24 实测真实库 ``user_version=44``、代码
到 v47，缺 ``ingest_event`` / ``conversation_message`` / ``conversation_job``
三表）：任何写入面首次碰缺表都会 ``no such table``。stdio 钩子进程独立于 API
服务、不跑 ``init_db``；HTTP 侧则可能连上被外部回滚/改坏的库。两处此前各自
应付——钩子里内联补建、HTTP 直接 500。本模块把「判定缺表 → 补跑 init_db →
允许调用方重试一次」收敛成单一真源，stdio 与 HTTP 通道共用。

纪律：
- **只认缺表**：``OperationalError`` 且消息含 ``no such table``；锁冲突/磁盘满
  等不重试——补建表治不了那些病，重试只会把失败拖长。
- **每个 engine 至多自愈一次**：schema 真坏（只读介质）时不至于每次写入都付
  一次 ``init_db`` 的代价；换库（测试夹具/运维搬迁）调 ``reset_heal_state()``。
- **修复失败不吞**：``heal_missing_schema()`` 原样上抛，调用方据此如实返回失败
  （钩子 ``ok=False``、HTTP 5xx），宁 miss 不脏写。
"""

from __future__ import annotations

import threading

from sqlalchemy.exc import OperationalError

_MISSING_TABLE_MARKER = "no such table"

_lock = threading.Lock()
# 已自愈过的 engine 身份。同时持有 engine 强引用：对象活着 id 就不会被回收后
# 让给新 engine——测试夹具每个用例换库，靠这一条不会把新库误判成「已修过」。
_healed: dict[int, object] = {}


def is_missing_table_error(exc: BaseException) -> bool:
    """是否「库里的表不存在」这一类 schema 漂移错误（其余错误不适用自愈）。"""
    return isinstance(exc, OperationalError) and _MISSING_TABLE_MARKER in str(exc).lower()


def heal_missing_schema() -> bool:
    """补跑一次 ``init_db()``（建表 + user_version 增量迁移，幂等）。

    返回 True = 本次真的跑了 ``init_db``；False = 该 engine 已经自愈过、直接复用。
    修复自身失败时原样上抛，并撤销登记让下一次写入还有机会再试。
    """
    from lantai.storage import db

    engine = db.engine
    key = id(engine)
    with _lock:
        if key in _healed:
            return False
        # 先登记再修复：自愈期间并发到达的写入不会各自再跑一遍 init_db，而是
        # 直接走「已修过」分支重试——修复失败的登记会在下面撤销。
        _healed[key] = engine
        try:
            db.init_db()
        except BaseException:
            _healed.pop(key, None)
            raise
    return True


def reset_heal_state() -> None:
    """清空自愈登记（换库/测试夹具用；生产单 engine 不需要调用）。"""
    with _lock:
        _healed.clear()
