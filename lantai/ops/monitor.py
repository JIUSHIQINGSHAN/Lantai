"""瞭望（监控面板）只读聚合——后台监控面板「瞭望」的统一数据源。

设计原则与 ops/overview.py 一致：build_monitor_snapshot(session) 是纯函数
（测试直传临时 session，不 mock 内部逻辑）；get_monitor_snapshot() 打开默认
会话执行。只读，零写放大，任何单子系统探测失败都降级为 degraded/unknown，
不让监控自身成为故障点。

面板回答的运维问题：
- 健康：SQLite / ChromaDB / LLM 端点现在通不通
- 队列：候选、提案、冲突、参数建议、结晶各积压多少（人工闸门水位）
- Worker：调度周期是多少、上次什么时候跑的、有没有漏跑（staleness）
- 检索：近 24h/7d 检索量、零召回率、平均延迟、token 成本
- 吞吐：近 7 天每日新增记忆与每日检索次数（双序列）
- 摄取：最近摄取任务成败、来源数
- 存储：SQLite 与 ChromaDB 磁盘占用
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import func, select

from lantai.core import scheduler
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    ConflictEvent,
    IngestJob,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    ParamAdviceRun,
    ParamSuggestion,
    RetrievalEvent,
    SkillCrystal,
    Source,
)
from lantai.storage import db

VERSION = "0.21.0"

# worker 中文名（案牍/调度统一用词）
WORKER_LABELS: dict[str, str] = {
    "ingest": "摄取",
    "evolve": "演化",
    "forgetting": "遗忘",
    "candidate_ttl": "候选沙汰",
    "digest": "每日盘点",
    "param_advice": "参数建议",
    "reflect": "反思蒸馏",
    "autodream": "雾梦蒸馏",
}

# 漏跑宽限：超过周期 × 此倍数仍未跑 → 标记 overdue（调度抖动不误报）
_STALE_GRACE = 1.5

_PROCESS_STARTED_AT = utcnow()


# ---------------------------------------------------------------- 基础工具

def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dir_size_bytes(path: str | Path) -> int | None:
    """目录递归大小（字节）；路径不存在返回 None。仅读元数据，不读文件内容。"""
    root = Path(path)
    if not root.exists():
        return None
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                fp = Path(dirpath) / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total


def _db_file_path() -> Path | None:
    """从 DATABASE_URL 解析 sqlite 文件路径（内存库/非 sqlite 返回 None）。"""
    url = settings.DATABASE_URL or ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):])
    return None


# ---------------------------------------------------------------- 健康探测

def probe_health(session) -> dict[str, dict[str, str]]:
    """三项依赖探测：sqlite 用传入 session 轻查询；chroma/llm 惰性导入。

    每项 {"status": ok|degraded|unknown, "detail": str}；探测异常不外抛。
    """
    checks: dict[str, dict[str, str]] = {}

    # SQLite（读 1 行，不加载数据）
    try:
        session.exec(select(MemoryItem.id).limit(1)).all()
        checks["sqlite"] = {"status": "ok", "detail": "可读写"}
    except Exception as exc:  # 监控不吞主链路，但自己不能炸
        checks["sqlite"] = {"status": "degraded", "detail": str(exc)[:200]}

    # ChromaDB（实例化即连接；不执行检索）
    try:
        from lantai.storage.vector_store import get_vector_store
        get_vector_store()
        checks["chromadb"] = {"status": "ok", "detail": "向量库可连接"}
    except Exception as exc:
        checks["chromadb"] = {"status": "degraded", "detail": str(exc)[:200]}

    # LLM 端点（未配置 key 时跳过，避免探活触发外部调用）
    if not settings.OPENAI_API_KEY:
        checks["llm"] = {"status": "unknown", "detail": "未配置 OPENAI_API_KEY"}
    else:
        try:
            from lantai.llm.client import get_client
            get_client().models.list()
            checks["llm"] = {"status": "ok", "detail": f"{settings.LLM_MODEL} 可达"}
        except Exception as exc:
            checks["llm"] = {"status": "degraded", "detail": str(exc)[:200]}

    return checks


def _overall_status(checks: dict[str, dict[str, str]]) -> str:
    """总体健康：任一 degraded → degraded；全 unknown → unknown；否则 ok。"""
    statuses = {c["status"] for c in checks.values()}
    if "degraded" in statuses:
        return "degraded"
    if not statuses or statuses == {"unknown"}:
        return "unknown"
    return "ok"


# ---------------------------------------------------------------- Worker 调度

def worker_specs() -> dict[str, dict[str, Any]]:
    """受监控的 worker 周期与启用状态（与案牍 worker_schedule_specs 同源口径）。"""
    return {
        "ingest": {"seconds": settings.INGEST_CRON_MINUTES * 60,
                   "enabled": True, "label": WORKER_LABELS["ingest"]},
        "evolve": {"seconds": settings.EVOLVE_CRON_MINUTES * 60,
                   "enabled": True, "label": WORKER_LABELS["evolve"]},
        "forgetting": {"seconds": settings.FORGET_CRON_HOURS * 3600,
                       "enabled": True, "label": WORKER_LABELS["forgetting"]},
        "candidate_ttl": {"seconds": settings.CANDIDATE_TTL_CRON_HOURS * 3600,
                          "enabled": True, "label": WORKER_LABELS["candidate_ttl"]},
        "digest": {"seconds": 86400, "enabled": settings.DIGEST_ENABLED,
                   "label": WORKER_LABELS["digest"]},
        "param_advice": {"seconds": settings.PARAM_ADVICE_CRON_MINUTES * 60,
                         "enabled": settings.PARAM_ADVICE_ENABLED,
                         "label": WORKER_LABELS["param_advice"]},
        "reflect": {"seconds": 86400, "enabled": settings.REFLECT_ENABLED,
                    "label": WORKER_LABELS["reflect"]},
        "autodream": {"seconds": settings.AUTODREAM_CRON_DAYS * 86400,
                      "enabled": settings.AUTODREAM_ENABLED,
                      "label": WORKER_LABELS["autodream"]},
    }


def _worker_state(name: str, spec: dict[str, Any], now: datetime,
                  started_at: datetime) -> dict[str, Any]:
    """单个 worker 的调度状态：last_run / age_seconds / state。

    state:
      - disabled  : 功能开关关闭
      - never_run : 启用后从未跑过（进程刚启动宽限 10 分钟不报 overdue）
      - ok        : 周期内正常运行
      - overdue   : 超过周期 × 宽限倍数仍未跑
    """
    if not spec.get("enabled", True):
        return {"name": name, "label": spec.get("label", name),
                "interval_seconds": spec["seconds"], "enabled": False,
                "last_run": None, "age_seconds": None, "state": "disabled"}

    last_run_iso = scheduler.get_last_run(name)
    last_run = _aware(_parse_dt(last_run_iso))
    age = (now - last_run).total_seconds() if last_run else None
    uptime = (now - started_at).total_seconds()

    if last_run is None:
        state = "ok" if uptime < 600 else "overdue"
        return {"name": name, "label": spec.get("label", name),
                "interval_seconds": spec["seconds"], "enabled": True,
                "last_run": None, "age_seconds": None, "state": state}

    threshold = spec["seconds"] * _STALE_GRACE
    state = "overdue" if age is not None and age > threshold else "ok"
    return {"name": name, "label": spec.get("label", name),
            "interval_seconds": spec["seconds"], "enabled": True,
            "last_run": last_run_iso, "age_seconds": int(age) if age is not None else None,
            "state": state}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 检索质量

def retrieval_stats(session, now: datetime) -> dict[str, Any]:
    """检索侧聚合：24h/7d 量、零召回率、延迟分位、token 成本、每日序列。

    零召回率排除系统注入噪音（is_system_noise），与 recall_report 同口径。
    """
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    events_24h = session.exec(
        select(RetrievalEvent).where(RetrievalEvent.created_at >= day_ago)).all()
    events_7d = session.exec(
        select(RetrievalEvent).where(RetrievalEvent.created_at >= week_ago)).all()

    def summarize(events: list[RetrievalEvent]) -> dict[str, Any]:
        real = [e for e in events if not e.is_system_noise]
        latencies = sorted(int(e.latency_ms or 0) for e in real if e.latency_ms)
        zero = sum(1 for e in real if e.zero_result)
        tokens = sum(int(e.estimated_tokens or 0) for e in real)

        def pct(p: float) -> float:
            if not latencies:
                return 0.0
            idx = min(len(latencies) - 1, int(len(latencies) * p))
            return float(latencies[idx])

        return {
            "total": len(events),
            "real": len(real),
            "system_noise": len(events) - len(real),
            "zero_recall": zero,
            "zero_recall_rate": round(zero / len(real), 4) if real else 0.0,
            "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "latency_p95_ms": pct(0.95),
            "estimated_tokens": tokens,
        }

    # 近 7 天每日检索次数（含噪音，看吞吐总量）
    daily: dict[str, int] = defaultdict(int)
    daily_zero: dict[str, int] = defaultdict(int)
    for e in events_7d:
        day = e.created_at.strftime("%Y-%m-%d") if e.created_at else "?"
        daily[day] += 1
        if not e.is_system_noise and e.zero_result:
            daily_zero[day] += 1
    base = (now - timedelta(days=6)).date()
    daily_series = []
    for i in range(7):
        day = str(base + timedelta(days=i))
        daily_series.append({
            "date": day,
            "retrievals": daily.get(day, 0),
            "zero_recall": daily_zero.get(day, 0),
        })

    # 最近 10 条慢查询（真实查询，延迟降序）
    slow = sorted(
        (e for e in events_7d if not e.is_system_noise),
        key=lambda e: e.latency_ms or 0, reverse=True)[:10]
    slow_queries = [{
        "query": (e.query_text or "")[:80],
        "latency_ms": e.latency_ms,
        "zero_result": e.zero_result,
        "lane": e.lane or "unknown",
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in slow]

    return {
        "last_24h": summarize(events_24h),
        "last_7d": summarize(events_7d),
        "daily_series": daily_series,
        "slow_queries": slow_queries,
    }


# ---------------------------------------------------------------- 吞吐

def memory_daily_series(session, now: datetime, days: int = 7) -> list[dict[str, Any]]:
    """近 N 天每日新增记忆数（缺日补零，与 /usage 同口径）。"""
    since = now - timedelta(days=days - 1)
    rows = session.exec(
        select(func.date(MemoryItem.created_at), func.count())
        .where(MemoryItem.created_at >= since)
        .group_by(func.date(MemoryItem.created_at))).all()
    counts = {str(d): int(c) for d, c in rows}
    base = (now - timedelta(days=days - 1)).date()
    return [{"date": str(base + timedelta(days=i)),
             "new_memories": counts.get(str(base + timedelta(days=i)), 0)}
            for i in range(days)]


# ---------------------------------------------------------------- 主快照

def build_monitor_snapshot(session, *, now: datetime | None = None,
                           process_started_at: datetime | None = None) -> dict[str, Any]:
    """只读聚合：监控面板全量快照。纯函数，session 由调用方注入。"""
    now = _aware(now) or utcnow()
    started = _aware(process_started_at) or _PROCESS_STARTED_AT

    # ---- 健康
    checks = probe_health(session)
    health = {"overall": _overall_status(checks), "checks": checks}

    # ---- 记忆存量与分布
    mem_total = session.exec(select(func.count()).select_from(MemoryItem)).one()
    mem_active = session.exec(
        select(func.count()).select_from(MemoryItem).where(
            MemoryItem.status == "active")).one()
    mem_archived = session.exec(
        select(func.count()).select_from(MemoryItem).where(
            MemoryItem.status == "archived")).one()
    by_lane = {k: int(v) for k, v in session.exec(
        select(MemoryItem.lane, func.count()).group_by(MemoryItem.lane)).all()}
    by_decay_class = {k: int(v) for k, v in session.exec(
        select(MemoryItem.decay_class, func.count())
        .group_by(MemoryItem.decay_class)).all()}
    by_tier = {k: int(v) for k, v in session.exec(
        select(MemoryItem.tier, func.count()).group_by(MemoryItem.tier)).all()}

    # ---- 人工闸门队列水位
    queues = {
        "candidates_pending": int(session.exec(
            select(func.count()).select_from(MemoryCandidate).where(
                MemoryCandidate.status == "pending_review")).one()),
        "proposals_pending": int(session.exec(
            select(func.count()).select_from(MemoryProposal).where(
                MemoryProposal.status == "pending")).one()),
        "conflicts_open": int(session.exec(
            select(func.count()).select_from(ConflictEvent).where(
                ConflictEvent.status == "open")).one()),
        "param_suggestions_pending": int(session.exec(
            select(func.count()).select_from(ParamSuggestion).where(
                ParamSuggestion.status == "pending")).one()),
        "crystals_candidate": int(session.exec(
            select(func.count()).select_from(SkillCrystal).where(
                SkillCrystal.status == "candidate")).one()),
    }

    # ---- Worker 调度
    specs = worker_specs()
    workers = [_worker_state(name, spec, now, started) for name, spec in specs.items()]
    workers_overdue = [w["name"] for w in workers if w["state"] == "overdue"]

    # ---- 检索质量与吞吐
    retrieval = retrieval_stats(session, now)
    memories_daily = memory_daily_series(session, now, days=7)
    # 合并双序列（日期对齐）
    retrieval_by_day = {d["date"]: d for d in retrieval["daily_series"]}
    throughput = [{
        "date": d["date"],
        "new_memories": d["new_memories"],
        "retrievals": retrieval_by_day.get(d["date"], {}).get("retrievals", 0),
    } for d in memories_daily]
    retrieval["daily_series"] = throughput  # 前端单图双序列

    # ---- 摄取任务（最近 10 条 + 来源统计）
    recent_jobs = session.exec(
        select(IngestJob).order_by(IngestJob.started_at.desc()).limit(10)).all()
    jobs_failed_24h = session.exec(
        select(func.count()).select_from(IngestJob).where(
            IngestJob.status == "failed",
            IngestJob.started_at >= now - timedelta(hours=24))).one()
    sources_total = session.exec(select(func.count()).select_from(Source)).one()
    sources_enabled = session.exec(
        select(func.count()).select_from(Source).where(Source.enabled == True)).one()  # noqa: E712
    ingestion = {
        "sources_total": int(sources_total),
        "sources_enabled": int(sources_enabled),
        "jobs_failed_24h": int(jobs_failed_24h),
        "recent_jobs": [{
            "id": j.id,
            "source_id": j.source_id,
            "status": j.status,
            "error": (j.error or "")[:200],
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        } for j in recent_jobs],
    }

    # ---- 参数建议最近运行
    latest_param_run = session.exec(
        select(ParamAdviceRun).order_by(ParamAdviceRun.created_at.desc()).limit(1)).first()

    # ---- 潮波缓冲水位
    try:
        from lantai.ingestion.coalesce import get_coalesce_buffer
        buffer = get_coalesce_buffer().water_level()
    except Exception:
        buffer = {}

    # ---- 存储占用
    db_path = _db_file_path()
    storage = {
        "sqlite_bytes": db_path.stat().st_size if db_path and db_path.exists() else None,
        "chromadb_bytes": _dir_size_bytes(settings.CHROMADB_PATH),
        "db_path": str(db_path) if db_path else None,
        "chromadb_path": settings.CHROMADB_PATH or None,
    }

    # ---- 运行时
    uptime_seconds = (now - started).total_seconds()
    runtime = {
        "version": VERSION,
        "started_at": started.isoformat(),
        "uptime_seconds": int(uptime_seconds),
        "server_time": now.isoformat(timespec="seconds"),
        "host": settings.HOST,
        "port": settings.PORT,
        "scheduler_enabled": bool(settings.LANTAI_RUN_SCHEDULER),
        "features": {
            "digest": settings.DIGEST_ENABLED,
            "reflect": settings.REFLECT_ENABLED,
            "autodream": settings.AUTODREAM_ENABLED,
            "param_advice": settings.PARAM_ADVICE_ENABLED,
            "coalesce": settings.COALESCE_ENABLED,
            "reranker": settings.RERANKER_ENABLED,
            "scene_layer": settings.SCENE_LAYER_ENABLED,
        },
    }

    # ---- 告警汇总（面板顶部红灯来源）
    alerts: list[dict[str, str]] = []
    if health["overall"] == "degraded":
        for name, c in checks.items():
            if c["status"] == "degraded":
                alerts.append({"level": "critical", "code": f"health:{name}",
                               "message": f"{name} 探测异常：{c['detail']}"})
    for name in workers_overdue:
        alerts.append({"level": "warning", "code": f"worker:{name}",
                       "message": f"{WORKER_LABELS.get(name, name)} worker 超过调度周期未运行"})
    if queues["candidates_pending"] > 50:
        alerts.append({"level": "warning", "code": "queue:candidates",
                       "message": f"待审候选积压 {queues['candidates_pending']} 条"})
    if queues["conflicts_open"] > 0:
        alerts.append({"level": "warning", "code": "queue:conflicts",
                       "message": f"{queues['conflicts_open']} 条未消解冲突"})
    if ingestion["jobs_failed_24h"] > 0:
        alerts.append({"level": "critical", "code": "ingest:failed",
                       "message": f"近 24h {ingestion['jobs_failed_24h']} 个摄取任务失败"})
    r24 = retrieval["last_24h"]
    if r24["real"] >= 10 and r24["zero_recall_rate"] >= 0.5:
        alerts.append({"level": "warning", "code": "retrieval:zero",
                       "message": f"近 24h 零召回率 {r24['zero_recall_rate'] * 100:.0f}%（{r24['real']} 次真实检索）"})

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "health": health,
        "alerts": alerts,
        "runtime": runtime,
        "storage": storage,
        "memories": {
            "total": int(mem_total),
            "active": int(mem_active),
            "archived": int(mem_archived),
            "by_lane": by_lane,
            "by_decay_class": by_decay_class,
            "by_tier": by_tier,
        },
        "queues": queues,
        "workers": workers,
        "retrieval": retrieval,
        "ingestion": ingestion,
        "coalesce_buffer": buffer,
        "latest_param_run": {
            "id": latest_param_run.id,
            "status": latest_param_run.status,
            "created_at": latest_param_run.created_at.isoformat()
            if latest_param_run and latest_param_run.created_at else None,
        } if latest_param_run else None,
    }


def get_monitor_snapshot() -> dict[str, Any]:
    """打开默认会话执行快照（只读）。"""
    with db.get_session() as s:
        return build_monitor_snapshot(s)
