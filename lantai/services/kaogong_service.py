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


def _kaogong_scope(principal):
    """考功候选集的归属条件（票 .scratch/readside-gaps/12；None 口径见票
    `.scratch/mcp-identity-gaps/06`）。

    非 admin：`user_id == viewer OR IS NULL`；admin / 显式系统身份 `__system__`
    → None（不过滤）。

    NULL 口径同票 03/04/06/09/10：真实库 615 行 `user_id IS NULL` 的
    memoryitem（迁移前/脚本直插），判"不可见"会让单人部署下的考功直接
    空转。NULL 是「未记录」不是「属于所有人」。

    **`principal=None` 收敛到 `"default"`，不再返回 None**（票 06）：
    旧 docstring 声称 None 是"worker/CLI/scheduler 保持全表"，
    grep 实证**这些调用方一个都不存在**——`run_kaogong_cycle` 的全部调用方
    只有 `routes_evolution.py:104`（HTTP）与 `mcp.py:321`（MCP）。
    MCP 入口的 None 语义是「宿主没透传身份」，**不是**「内部 worker 全量」，
    按旧口径走就是让无身份调用改写全库每条记忆的 tier/importance。
    定时考功若要全表，显式传 `Principal(user_id="__system__")`——见
    `run_kaogong_cycle` 与 `test_mcp_none_scope_leaks.py`。
    """
    from lantai.core.acl import SYSTEM_VIEWER
    from lantai.services.work_item_service import _viewer_of

    if bool(getattr(principal, "is_admin", False)):
        return None
    viewer = _viewer_of(principal)
    if viewer == SYSTEM_VIEWER:
        return None
    return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))


def run_kaogong_cycle(session: Session | None = None, principal=None) -> dict:
    """执行一次考功评定周期。

    归属（票 .scratch/readside-gaps/12）：此前候选集是全表
    `select(MemoryItem).where(status=="active")`，一个身份都不取——
    任何持有 API key 者都能借此改写**别人**记忆的
    `tier` / `decay_class` / `importance`（第七轮实证 0.9 → 0.1，
    且 importance 降下去没有回滚路径）。读侧缺口只是"看到"，
    这里是真改，所以宁 miss 不脏写：收窄后没有可评估的记忆就返回
    全 0 报告，不去动别人的。
    """
    global _LATEST_KAOGONG_REPORT

    def _run(s: Session) -> dict:
        q = select(MemoryItem).where(MemoryItem.status == "active")
        scope = _kaogong_scope(principal)
        if scope is not None:
            q = q.where(scope)
        items = s.exec(q).all()
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
