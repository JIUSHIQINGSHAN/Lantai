"""观测面保留清扫（票 29）：retrieval_event / llm_call_event 行级 TTL。

病灶：三条观测链只有 `operation_logs` 有保留期（telemetry.TelemetryWriter.prune，
MONITOR_RETENTION_DAYS 范式点）——retrieval_event 与 llm_call_event 是全库最高频
写入表（检索与 LLM 调用逐次落库），零行级清理无界增长，叠加观察窗/回执对账/
usage 统计类查询随月份线性劣化。

拍板（票面 Answer）：
- 不复用 MONITOR_RETENTION_DAYS——取证窗与观察窗语义不同源；两表各配独立
  旋钮（RETRIEVAL_EVENT_RETENTION_DAYS / LLM_CALL_EVENT_RETENTION_DAYS）。
- TTL 下限结构性钳制：retrieval_event 不得短于观察窗
  （RECALL_MONITOR_WINDOW_DAYS，recall_report 窗口计数 ADR-0027；回执对账窗
  RECEIPT_MISS_TIMEOUT_SECONDS=300s 远小于天粒度，结构性满足）——TTL 短于
  观察窗会让窗口计数静默失真，宁可钳制 + warning 留痕（宁 visible 不静默）。
  llm_call_event 的司天降级率窗 24h（monitor.collect_llm_degrade）低于任何
  天粒度 TTL，max(days, 1) 即结构性满足。
- 边界语义与 telemetry.prune 同源：`created_at < cutoff` 严格小于（cutoff 恰在
  行上的行保留，回归锁钉死防 `<=` 抖动）；幂等（二跑 0）；返回删除条数；
  失败吞掉返回 0（清扫绝不反噬调度链，宁 miss 不脏写）。
- 与票 10 分层：票 10 的抹文是对留存行的内容脱敏，本票是对超龄行的物理删除
  （容量）；两者叠加后老行整体消失，互不冲突。ADR-0084 §2.7 证据留存边界：
  证据留存 = TTL 内。

OBSERVABILITY_RETENTION_TABLES 是发布门禁 retention_symmetry 对账的单一真源
（scripts/release_check.py 静态对账：旋钮在 settings、清扫函数在本仓、scheduler
挂载 prune_all、telemetry 自扫 operation_logs）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete as sa_delete

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import LLMCallEvent, RetrievalEvent

# 观测面保留清单（门禁对账真源）：表 → 保留旋钮 / 清扫函数 / 定义模块。
# operation_logs 的清扫是 telemetry.TelemetryWriter.prune（flush 后自调），
# 行为以现状锁（tests/test_observability_retention.py 对照臂）钉死，不搬家。
OBSERVABILITY_RETENTION_TABLES: dict[str, dict[str, str]] = {
    "operation_logs": {
        "knob": "MONITOR_RETENTION_DAYS",
        "module": "lantai/observability/telemetry.py",
        "sweep": "prune",
    },
    "retrieval_event": {
        "knob": "RETRIEVAL_EVENT_RETENTION_DAYS",
        "module": "lantai/observability/retention.py",
        "sweep": "prune_retrieval_events",
    },
    "llm_call_event": {
        "knob": "LLM_CALL_EVENT_RETENTION_DAYS",
        "module": "lantai/observability/retention.py",
        "sweep": "prune_llm_call_events",
    },
}

# 最近一轮清扫留痕（司天面 retention_stats 的数据源；测试用 reset_stats 归零）
_LAST_SWEEPS: dict[str, dict] = {}


def reset_stats() -> None:
    """清空最近清扫留痕（测试用；生产链路只在进程首扫前为空）。"""
    _LAST_SWEEPS.clear()


def _effective_days(days: int, floor_days: int, table: str, *, warn: bool = True) -> int:
    """保留期下限钳制：低于观察窗下限时按窗执行并留痕（宁 visible 不静默失真）。"""
    days = max(1, int(days))
    effective = max(days, int(floor_days))
    if warn and effective != days:
        logger.warning(
            "观测面保留期钳制：%s 配置 %d 天低于观察窗下限 %d 天，按 %d 天执行"
            "（观察窗计数不得静默失真，票 29）",
            table,
            days,
            floor_days,
            effective,
        )
    return effective


def _prune_table(model, *, table: str, days: int, floor_days: int, now: datetime | None) -> int:
    moment = now or utcnow()
    effective = _effective_days(days, floor_days, table)
    cutoff = moment - timedelta(days=effective)
    try:
        from lantai.storage import db

        with db.get_session() as s:
            result = s.exec(
                sa_delete(model)
                .where(model.created_at < cutoff)
                .execution_options(synchronize_session=False)
            )
            s.commit()
            deleted = int(getattr(result, "rowcount", 0) or 0)
        _LAST_SWEEPS[table] = {"rows_deleted": deleted, "pruned_at": moment.isoformat()}
        if deleted:
            logger.info(
                "观测面清扫 %s：删除 %d 行超龄数据（保留 %d 天，票 29）", table, deleted, effective
            )
        else:
            logger.debug("观测面清扫 %s：无超龄行（保留 %d 天）", table, effective)
        return deleted
    except Exception:
        logger.exception("观测面清扫失败（非致命，清扫不得反噬调度链）：%s", table)
        return 0


def prune_retrieval_events(now: datetime | None = None) -> int:
    """检索事件 TTL 清扫：删 `created_at` 早于 cutoff 的行，返回删除条数。

    下限钳制到观察窗（RECALL_MONITOR_WINDOW_DAYS）：recall_report 的窗口计数
    （ADR-0027）与回执对账（ADR-0049）的统计口径依赖窗内数据齐全。"""
    return _prune_table(
        RetrievalEvent,
        table="retrieval_event",
        days=int(settings.RETRIEVAL_EVENT_RETENTION_DAYS),
        floor_days=int(settings.RECALL_MONITOR_WINDOW_DAYS),
        now=now,
    )


def prune_llm_call_events(now: datetime | None = None) -> int:
    """LLM 外呼结局 TTL 清扫：删 `created_at` 早于 cutoff 的行，返回删除条数。

    只记标量（channel/outcome/duration_ms）零正文；司天 24h 降级率窗
    （monitor.collect_llm_degrade）由天粒度 TTL 结构性覆盖。"""
    return _prune_table(
        LLMCallEvent,
        table="llm_call_event",
        days=int(settings.LLM_CALL_EVENT_RETENTION_DAYS),
        floor_days=1,
        now=now,
    )


def prune_all(now: datetime | None = None) -> dict[str, int]:
    """scheduler 挂载入口（observability_purge，每小时）：逐表清扫，返回删除数。"""
    return {
        "retrieval_event": prune_retrieval_events(now=now),
        "llm_call_event": prune_llm_call_events(now=now),
    }


def retention_stats() -> dict:
    """司天面透出（对齐 telemetry.stats() 范式）：逐表行数 / 保留天数 /
    最近清理时间与删除数。行数真实计数；查询失败如实 None（不编造）。"""
    faces = (
        (
            "retrieval_event",
            RetrievalEvent,
            settings.RETRIEVAL_EVENT_RETENTION_DAYS,
            settings.RECALL_MONITOR_WINDOW_DAYS,
        ),
        ("llm_call_event", LLMCallEvent, settings.LLM_CALL_EVENT_RETENTION_DAYS, 1),
    )
    out: dict = {}
    from sqlmodel import func, select

    from lantai.storage import db

    for table, model, days, floor in faces:
        last = _LAST_SWEEPS.get(table)
        try:
            with db.get_session() as s:
                rows = int(s.exec(select(func.count()).select_from(model)).one())
        except Exception:
            logger.exception("retention_stats 行数统计失败（非致命）：%s", table)
            rows = None
        out[table] = {
            "days": _effective_days(days, floor, table, warn=False),
            "rows": rows,
            "last_pruned_at": (last or {}).get("pruned_at"),
            "last_deleted": (last or {}).get("rows_deleted"),
        }
    return out
