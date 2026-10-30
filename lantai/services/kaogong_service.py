"""考功（ADR-0031 / ADR-0078）：长程反馈驱动的记忆价值演化与升降评定核心服务。

基于认知层 KaogongEngine 统一封装：
1. evaluate_memory_item_grade: 纯函数评估单条记忆功过（保持历史契约兼容）；
2. run_kaogong_cycle: 批量执行升降级并落库；
3. get_kaogong_report: 获取最新考功评定审计报告；
4. kaogong_inspect_service: 单条记忆多因子价值深度考评。
"""

from typing import Any

from sqlmodel import Session

from lantai.cognition.kaogong import KaogongEngine, KaogongResult
from lantai.models.tables import MemoryItem
from lantai.storage import db


def evaluate_memory_item_grade(memory: MemoryItem) -> dict[str, Any]:
    """纯函数评估单条记忆的功过评级（基于 KaogongEngine，保持历史契约兼容）。"""
    res: KaogongResult = KaogongEngine.evaluate_item(memory)
    # 转换为服务层历史结构字典
    out: dict[str, Any] = {
        "action": res.action,
        "memory_id": res.memory_id,
        "reason": res.reason,
        "score": res.score,
        "score_factors": res.score_factors,
    }
    if res.action == "promote_longterm":
        out["new_tier"] = res.new_tier
        out["new_decay_class"] = "semantic"
        out["new_importance"] = res.new_importance
    elif res.action == "demote_deprecate":
        out["new_importance"] = res.new_importance
    return out


def run_kaogong_cycle(
    session: Session | None = None, principal=None, limit: int = 100
) -> dict[str, Any]:
    """执行一次考功评定周期。"""
    engine = KaogongEngine(session=session, principal=principal)
    return engine.run_cycle(limit=limit)


def get_kaogong_report(session: Session | None = None, principal=None) -> dict[str, Any]:
    """获取最新考功报告。若尚无报告则执行一次。"""
    engine = KaogongEngine(session=session, principal=principal)
    return engine.get_report()


def kaogong_inspect_service(
    memory_id: str,
    session: Session | None = None,
    principal=None,
    write: bool = False,
) -> dict[str, Any]:
    """对单条记忆执行深度多因子考功审查（只读审评或直接落库）。"""
    mem_id = (memory_id or "").strip()
    if not mem_id:
        raise ValueError("memory_id 不能为空")

    def _run(s: Session) -> dict[str, Any]:
        engine = KaogongEngine(session=s, principal=principal)
        if write:
            res = engine.evaluate_memory_id(mem_id)
        else:
            mem = s.get(MemoryItem, mem_id)
            if not mem:
                raise ValueError(f"记忆记录未找到: {mem_id}")
            if not engine._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
                raise PermissionError(f"无权查看此记忆考功详情（行级隔离）: {mem_id}")
            res = engine.evaluate_item(mem)
        return res.to_dict(fenced=True)

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)
