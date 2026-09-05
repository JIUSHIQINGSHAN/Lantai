"""考功（ADR-0031）：长程反馈驱动的记忆价值演化与升降评定核心服务。

提供：
1. evaluate_memory_item_grade: 纯函数评估单条记忆功过（上考晋升/下考降权/中考保持）；
2. run_kaogong_cycle: 遍历全库 active 记忆，批量执行升降级并落库；
3. get_kaogong_report: 获取最新考功评定审计报告。
"""
from typing import Any

from sqlmodel import Session, select

from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 缓存最近一次考功审计报告
_LATEST_KAOGONG_REPORT: dict[str, Any] = {
    "evaluated": 0,
    "promoted_longterm": 0,
    "demoted": 0,
    "kept": 0,
    "details": [],
    "evaluated_at": None,
}


def evaluate_memory_item_grade(memory: MemoryItem) -> dict:
    """纯函数评估单条记忆的功过评级（宁 miss 不脏写）。"""
    use_count = memory.use_count or 0
    helpful_count = memory.helpful_count or 0

    # 样本不足（use_count < 3）保持原状（宁 miss 不脏写）
    if use_count < 3:
        return {
            "action": "keep_neutral",
            "memory_id": memory.id,
            "reason": f"样本不足（use_count={use_count} < 3），保持原状",
        }

    helpful_ratio = helpful_count / use_count

    # 上考：高频高采纳（ratio >= 0.8）-> 晋升长期语义层
    if helpful_ratio >= 0.8:
        return {
            "action": "promote_longterm",
            "memory_id": memory.id,
            "new_tier": "longterm",
            "new_decay_class": "semantic",
            "new_importance": min(1.0, (memory.importance or 0.5) + 0.1),
            "reason": f"考功上考：高频高采纳（{helpful_count}/{use_count} = {helpful_ratio:.1%}），晋升长期语义层",
        }

    # 下考：高频低效（ratio <= 0.2）-> 降权至 0.1
    if helpful_ratio <= 0.2:
        return {
            "action": "demote_deprecate",
            "memory_id": memory.id,
            "new_importance": 0.1,
            "reason": f"考功下考：高频低效（{helpful_count}/{use_count} = {helpful_ratio:.1%}），降权至 0.1",
        }

    # 中考：表现平稳
    return {
        "action": "keep_neutral",
        "memory_id": memory.id,
        "reason": f"考功中考：采纳率平稳（{helpful_count}/{use_count} = {helpful_ratio:.1%}），保持原状",
    }


def run_kaogong_cycle(session: Session | None = None) -> dict:
    """全库执行一次考功评定周期。"""
    global _LATEST_KAOGONG_REPORT

    def _run(s: Session) -> dict:
        items = s.exec(
            select(MemoryItem).where(MemoryItem.status == "active")
        ).all()

        promoted_count = 0
        demoted_count = 0
        kept_count = 0
        details = []

        for mem in items:
            grade = evaluate_memory_item_grade(mem)
            action = grade["action"]

            if action == "promote_longterm":
                mem.tier = grade["new_tier"]
                mem.decay_class = grade["new_decay_class"]
                mem.importance = grade["new_importance"]
                s.add(mem)
                promoted_count += 1
                details.append({"id": mem.id, "action": action, "reason": grade["reason"]})
            elif action == "demote_deprecate":
                mem.importance = grade["new_importance"]
                s.add(mem)
                demoted_count += 1
                details.append({"id": mem.id, "action": action, "reason": grade["reason"]})
            else:
                kept_count += 1

        s.commit()
        report = {
            "evaluated": len(items),
            "promoted_longterm": promoted_count,
            "demoted": demoted_count,
            "kept": kept_count,
            "details": details,
            "evaluated_at": utcnow().isoformat(),
        }
        _LATEST_KAOGONG_REPORT = report
        logger.info(
            "考功周期完成：评估 %d 条记忆，晋升长期 %d 条，降权 %d 条，保持 %d 条",
            len(items),
            promoted_count,
            demoted_count,
            kept_count,
        )
        return report

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def get_kaogong_report(session: Session | None = None) -> dict:
    """获取最新考功报告。若尚无报告则执行一次。"""
    if _LATEST_KAOGONG_REPORT.get("evaluated_at") is None:
        return run_kaogong_cycle(session=session)
    return _LATEST_KAOGONG_REPORT
