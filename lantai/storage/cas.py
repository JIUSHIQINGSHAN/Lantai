"""裁决/认领状态机的条件更新（CAS）单一真源——票 22（X-06 收敛点）。

「SELECT 快照 → Python 判定 → 无条件 ORM 写回」在 pysqlite 惰性 BEGIN 下
零隔离（纯 SELECT 不进写事务），并发裁决即丢更新、终态复活、双认领。
范式孤本：`lantai/parameters/service.py`（ParamSuggestion 批准 CAS——全库
此前唯一一处条件 UPDATE），本模块把该范式收敛为一个入口，裁决/认领各处
统一调用；`UPDATE ... WHERE 主键 AND 期望态（+额外谓词）`，rowcount==0 即
并发败方——**错误语义由调用方决定**（按各自契约抛冲突 / 跳过 / 回滚），
本层不抛异常、不日志、不碰 ORM 对象。

pysqlite 语义要点（方案 A 成立的前提）：
- 条件 UPDATE 是事务内第一条 DML 时，惰性 BEGIN 此刻才开启写事务——语句
  对**最新已提交状态**求值（本 session 此前的 SELECT 快照不参与）；写锁经
  busy_timeout（db.py timeout=30s）排队，败方在胜方提交后重验 WHERE 落空。
- 胜方回滚（含异常路径）则认领一并撤销，无中间态残留、无需卡死回收——
  故认领应放「写阶段第一条 DML」，LLM/embed 网络调用留在认领之前
  （先无事务评估、后短事务条件提交）。
- `synchronize_session=False`：ORM 启用的 update 默认按**内存里的陈旧属性**
  评估同步条件，败方对象会被误标脏、搭下次 commit 顺车把旧值写回（恰是
  本票要修的竞态换马甲复现）。显式关闭，rowcount 是唯一事实。
"""

from typing import Any

from sqlalchemy import update
from sqlmodel import SQLModel


def cas_update(
    session: Any,
    model: type[SQLModel],
    row_id: str,
    expected: dict[str, Any],
    values: dict[str, Any],
    *extra_where: Any,
) -> int:
    """条件更新单行：`WHERE 主键 = row_id AND 期望态等值 AND extra_where`。

    expected 的键为 model 列名，值是期望状态（list/tuple/set → IN，覆盖
    「new|retry」这类多可认领态）；values 直接进 SET；extra_where 追加任意
    SQLA 谓词（如「截止时刻在写时重验」）。返回 rowcount：1 = 本调用者赢得
    这次状态迁移；0 = 期望态不成立（并发败方或 precondition 失效）。
    """
    conds = [model.id == row_id]
    for col, want in expected.items():
        column = getattr(model, col)
        conds.append(column.in_(want) if isinstance(want, (list, tuple, set)) else column == want)
    conds.extend(extra_where)
    result = session.exec(
        update(model).where(*conds).values(**values).execution_options(synchronize_session=False)
    )
    return result.rowcount


def atomic_update(
    session: Any,
    model: type[SQLModel],
    row_id: str,
    values: dict[str, Any],
    *extra_where: Any,
) -> int:
    """单语句原子更新（票 23，计数器/相对量写）：SET 里的 SQL 表达式（如
    `use_count + 1`、`min(1.0, importance + :delta)`）由 SQLite 对**当前已
    提交值**逐语句求值——两次并发各 +1 都落库，语义与串行执行逐字一致，
    不存在「读快照 → Python 算术 → 写回」的丢失窗口。WHERE 主键 +
    extra_where（写时重验判定输入）；rowcount==0 = 谓词不成立（行已消失或
    判定输入已变），错误语义由调用方决定（跳过 / 冲突 / 重读重试）。

    `synchronize_session=False` 承重理由同 cas_update：ORM 默认按内存陈旧
    属性评估同步，会把旧值搭下次 commit 顺车写回。rowcount 是唯一事实。
    """
    conds = [model.id == row_id, *extra_where]
    result = session.exec(
        update(model).where(*conds).values(**values).execution_options(synchronize_session=False)
    )
    return result.rowcount
