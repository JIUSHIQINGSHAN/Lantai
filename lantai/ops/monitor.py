"""司天（ADR-0044）：后台运行监控聚合——把散落的运行事实拼成一张可判断的快照。

分层（全部只读，不写库、不改语义）：

- `collect_*`：单一事实域采集（进程 / 存储 / 记忆 / 管道 / 调度 / 请求 / 安全 / 依赖）；
- `build_monitor_snapshot`：拼装 + `evaluate_alerts` 规则告警；
- `render_prometheus`：同一快照的 Prometheus 文本视图（外部监控可直接抓取）。

口径复用（不复制业务规则，ADR-0001 门面铁律）：
- 记忆分布复用 `lantai.ops.overview.build_overview`；
- worker 周期复用 `work_item_service.worker_schedule_specs`；
- 逾期判定复用 `core.scheduler.worker_staleness`（与案牍同源）；
- 零召回率复用 `observability.recall_report`。
"""

from __future__ import annotations

import os
import platform
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import func, select

from lantai.core import scheduler as scheduler_module
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    ApiKey,
    ConflictEvent,
    IngestJob,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    OperationLog,
    ParamAdviceRun,
    ReflectRun,
    SchedulerRun,
)
from lantai.observability import metrics as metrics_module
from lantai.observability.metrics import PROCESS_STARTED_AT, process_stats
from lantai.observability.recall_report import recall_report
from lantai.observability.telemetry import get_writer
from lantai.ops.overview import build_overview
from lantai.services.work_item_service import worker_schedule_specs
from lantai.storage import db

# 监控面板关心的核心表（缺表按 0 计，不炸）
_CORE_TABLES = (
    "memoryitem",
    "memorycandidate",
    "memoryproposal",
    "memorycheckpoint",
    "conflictevent",
    "rawdocument",
    "retrieval_event",
    "operation_logs",
    "scheduler_run",
    "reflect_run",
    "ingestjob",
    "source",
)


# ── 采集：存储 ─────────────────────────────────────────────────────────
def _db_path() -> Path | None:
    url = settings.DATABASE_URL or ""
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    return Path(url.removeprefix("sqlite:///"))


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def collect_storage_metrics(session) -> dict:
    """SQLite / FTS5 / ChromaDB 体积与行数（PRAGMA 直读，不整表加载）。"""
    path = _db_path()
    db_bytes = wal_bytes = 0
    available = False
    if path and path.exists():
        available = True
        db_bytes = path.stat().st_size
        wal = path.with_name(path.name + "-wal")
        if wal.exists():
            wal_bytes = wal.stat().st_size

    page_count = page_size = 0
    counts: dict[str, int] = {}
    try:
        conn = session.connection()
        page_count = int(conn.exec_driver_sql("PRAGMA page_count").scalar() or 0)
        page_size = int(conn.exec_driver_sql("PRAGMA page_size").scalar() or 0)
        existing = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in _CORE_TABLES:
            if table not in existing:
                counts[table] = 0
                continue
            counts[table] = int(conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() or 0)
        counts["memory_fts"] = (
            int(conn.exec_driver_sql("SELECT COUNT(*) FROM memory_fts").scalar() or 0)
            if "memory_fts" in existing
            else 0
        )
    except Exception:
        counts = counts or {}

    chroma_path = Path(settings.CHROMADB_PATH) if settings.CHROMADB_PATH else None
    vector = {
        "available": False,
        "path": str(chroma_path) if chroma_path else "",
        "bytes": 0,
        "collection_count": None,
    }
    if chroma_path and chroma_path.exists():
        vector["available"] = True
        vector["bytes"] = _dir_size(chroma_path)
        try:
            from lantai.storage.vector_store import get_vector_store

            store = get_vector_store()
            collection = getattr(store, "_collection", None)
            if collection is not None:
                vector["collection_count"] = int(collection.count())
        except Exception:
            vector["collection_count"] = None

    return {
        "database": {
            "available": available,
            "path": str(path) if path else "",
            "bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "mb": round(db_bytes / (1024 * 1024), 2),
            "page_count": page_count,
            "page_size": page_size,
            "schema_version": _schema_version(session),
        },
        "table_rows": counts,
        "vector_store": vector,
    }


def _schema_version(session) -> int:
    try:
        return int(session.connection().exec_driver_sql("PRAGMA user_version").scalar() or 0)
    except Exception:
        return 0


# ── 采集：记忆管道 ─────────────────────────────────────────────────────
def collect_pipeline_metrics(session, *, now: datetime | None = None) -> dict:
    """摄取 → 闸门 → 演化 → 遗忘 链路的积压与最近运行（只读聚合）。"""
    now = now or utcnow()
    day_ago = now - timedelta(days=1)

    pending_review = int(
        session.exec(
            select(func.count())
            .select_from(MemoryCandidate)
            .where(MemoryCandidate.status == "pending_review")
        ).one()
    )
    stale_candidates = int(
        session.exec(
            select(func.count())
            .select_from(MemoryCandidate)
            .where(MemoryCandidate.status == "pending_review", MemoryCandidate.created_at < day_ago)
        ).one()
    )
    proposals_pending = int(
        session.exec(
            select(func.count())
            .select_from(MemoryProposal)
            .where(MemoryProposal.status == "pending")
        ).one()
    )
    conflicts_open = int(
        session.exec(
            select(func.count()).select_from(ConflictEvent).where(ConflictEvent.status == "open")
        ).one()
    )
    archived_24h = int(
        session.exec(
            select(func.count())
            .select_from(MemoryItem)
            .where(MemoryItem.status == "archived", MemoryItem.updated_at >= day_ago)
        ).one()
    )

    ingest_rows = session.exec(
        select(IngestJob.status, func.count()).group_by(IngestJob.status)
    ).all()
    latest_ingest = session.exec(select(IngestJob).order_by(IngestJob.started_at.desc())).first()
    latest_param = session.exec(
        select(ParamAdviceRun).order_by(ParamAdviceRun.created_at.desc())
    ).first()
    latest_reflect = session.exec(select(ReflectRun).order_by(ReflectRun.run_at.desc())).first()

    from lantai.ingestion.coalesce import get_coalesce_buffer

    return {
        "candidates_pending_review": pending_review,
        "candidates_pending_over_24h": stale_candidates,
        "proposals_pending": proposals_pending,
        "conflicts_open": conflicts_open,
        "archived_last_24h": archived_24h,
        "coalesce": {
            "enabled": bool(settings.COALESCE_ENABLED),
            **get_coalesce_buffer().water_level(),
        },
        "ingest_jobs_by_status": {str(k): int(v) for k, v in ingest_rows},
        "latest_ingest_job": (
            {
                "id": latest_ingest.id,
                "status": latest_ingest.status,
                "started_at": latest_ingest.started_at.isoformat()
                if latest_ingest.started_at
                else None,
                "error": (latest_ingest.error or "")[:200],
            }
            if latest_ingest
            else None
        ),
        "latest_param_advice_run": (
            {
                "id": latest_param.id,
                "status": latest_param.status,
                "error_code": latest_param.error_code or "",
                "created_at": latest_param.created_at.isoformat()
                if latest_param.created_at
                else None,
            }
            if latest_param
            else None
        ),
        "latest_reflect_run": (
            {
                "id": latest_reflect.id,
                "run_at": latest_reflect.run_at.isoformat() if latest_reflect.run_at else None,
                "source": latest_reflect.source,
                "skipped": latest_reflect.skipped,
                "curate_failed": bool(latest_reflect.curate_failed),
                "rejecter_failed": int(latest_reflect.rejecter_failed or 0),
                "error": (latest_reflect.error or "")[:200],
            }
            if latest_reflect
            else None
        ),
    }


# ── 采集：调度器与 worker ──────────────────────────────────────────────
def collect_scheduler_metrics(session, *, now: datetime | None = None) -> dict:
    """APScheduler 作业清单 + 每个 worker 的上次运行与逾期判定。"""
    now = now or utcnow()
    last_runs = {row.name: row.last_run_utc for row in session.exec(select(SchedulerRun)).all()}
    workers = []
    for name, spec in worker_schedule_specs().items():
        period = int(spec["seconds"])
        enabled = bool(spec["enabled"])
        last_run = _parse_iso(last_runs.get(name))
        state = scheduler_module.worker_staleness(
            name,
            period_seconds=period,
            last_run=last_run,
            now=now,
            process_started_at=PROCESS_STARTED_AT,
        )
        workers.append(
            {
                "name": name,
                "enabled": enabled,
                "period_seconds": period,
                "last_run": state["last_run"],
                "last_run_age_seconds": state["last_run_age_seconds"],
                "due_at": state["due_at"],
                "overdue": bool(enabled and state["overdue"]),
                "critical": bool(enabled and state["critical"]),
                "status": (
                    "disabled"
                    if not enabled
                    else (
                        "critical"
                        if state["critical"]
                        else ("overdue" if state["overdue"] else state["status"])
                    )
                ),
                "manual_run": name in _MANUAL_WORKERS,
            }
        )
    workers.sort(
        key=lambda row: (
            {"critical": 0, "overdue": 1, "unknown": 2, "never": 3, "ok": 4, "disabled": 5}[
                row["status"]
            ],
            row["name"],
        )
    )
    status = scheduler_module.scheduler_status()
    return {
        "running": status["running"],
        "configured": status["configured"],
        "job_count": status["job_count"],
        "jobs": status["jobs"],
        "workers": workers,
        "overdue_count": sum(1 for w in workers if w["overdue"]),
        "process_started_at": PROCESS_STARTED_AT.isoformat(),
    }


_MANUAL_WORKERS = frozenset(
    {
        "ingest",
        "evolve",
        "forgetting",
        "candidate_ttl",
        "digest",
        "param_advice",
        "reflect",
        "autodream",
    }
)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from datetime import UTC

        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


# ── 采集：安全与依赖 ───────────────────────────────────────────────────
def collect_security_view(session) -> dict:
    """绑定/鉴权现状（不输出任何密钥明文，只给布尔与计数）。"""
    loopback = settings.HOST in {"127.0.0.1", "localhost", "::1"}
    try:
        keys_total = int(session.exec(select(func.count()).select_from(ApiKey)).one())
        keys_active = int(
            session.exec(
                select(func.count()).select_from(ApiKey).where(ApiKey.is_active == True)
            ).one()  # noqa: E712
        )
    except Exception:
        keys_total = keys_active = 0
    return {
        "host": settings.HOST,
        "port": int(settings.PORT),
        "loopback": loopback,
        "api_key_configured": bool(settings.API_KEY),
        "api_keys_total": keys_total,
        "api_keys_active": keys_active,
        "acl_bindings": len(settings.AGENT_LANE_BINDINGS or {}),
        # 实际生效的鉴权口径：api_keys 表有记录 = Bearer 校验；否则无 Authorization
        # 头的请求走 get_current_user 的 dev 回退（等于放行）
        "effective_auth": (
            "bearer_table" if keys_total else ("dev_fallback" if settings.API_KEY else "none")
        ),
        "telemetry": get_writer().stats(),
    }


def collect_dependency_view() -> dict:
    """外部依赖配置态（不做网络探活——那是 `/health/deep` 的职责）。"""
    chroma_path = Path(settings.CHROMADB_PATH) if settings.CHROMADB_PATH else None
    return {
        "llm": {
            "configured": bool(settings.OPENAI_API_KEY),
            "model": settings.LLM_MODEL,
            "embed_model": settings.EMBED_MODEL,
            "base_url": settings.OPENAI_BASE_URL,
        },
        "reranker": {
            "enabled": bool(settings.RERANKER_ENABLED),
            "model": settings.RERANKER_MODEL,
            "base_url": settings.RERANKER_BASE_URL,
        },
        "vector_store": {
            "type": settings.VECTOR_STORE_TYPE,
            "path_ready": bool(chroma_path and chroma_path.exists()),
        },
        "features": {
            "scheduler": bool(settings.LANTAI_RUN_SCHEDULER),
            "coalesce": bool(settings.COALESCE_ENABLED),
            "digest": bool(settings.DIGEST_ENABLED),
            "reflect": bool(settings.REFLECT_ENABLED),
            "param_advice": bool(settings.PARAM_ADVICE_ENABLED),
            "autodream": bool(settings.AUTODREAM_ENABLED),
            "scene_layer": bool(settings.SCENE_LAYER_ENABLED),
            "monitor": bool(settings.MONITOR_ENABLED),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count() or 0,
        },
    }


# ── 告警规则 ───────────────────────────────────────────────────────────
def _alert(
    alert_id: str,
    severity: str,
    title: str,
    detail: str,
    suggestion: str,
    metric: dict | None = None,
) -> dict:
    return {
        "id": alert_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
        "metric": metric or {},
    }


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def evaluate_alerts(snapshot: dict) -> list[dict]:
    """规则告警（纯函数，输入即快照）：只依据可核对的指标，不猜原因。"""
    alerts: list[dict] = []
    scheduler_view = snapshot.get("scheduler", {})
    requests = snapshot.get("requests", {}).get("window", {})
    storage = snapshot.get("storage", {})
    pipeline = snapshot.get("pipeline", {})
    security = snapshot.get("security", {})
    dependency = snapshot.get("dependency", {})
    quality = snapshot.get("quality", {})

    if scheduler_view.get("configured") and not scheduler_view.get("running"):
        alerts.append(
            _alert(
                "scheduler_not_running",
                "critical",
                "调度器未运行",
                "LANTAI_RUN_SCHEDULER=true 但 APScheduler 未启动，定时摄取/演化/遗忘全部停摆。",
                "检查启动日志；确认单进程部署（--workers 1）后重启服务。",
            )
        )

    for worker in scheduler_view.get("workers", []):
        if worker.get("overdue"):
            age = worker.get("last_run_age_seconds")
            alerts.append(
                _alert(
                    f"worker_overdue:{worker['name']}",
                    "critical" if worker.get("critical") else "high",
                    f"worker {worker['name']} 逾期未运行",
                    f"周期 {worker.get('period_seconds')}s，上次完成距今 "
                    f"{round(age / 3600, 1) if age is not None else '—'} 小时。",
                    f"手动触发：POST /monitor/workers/{worker['name']}/run",
                    {"worker": worker["name"], "last_run": worker.get("last_run")},
                )
            )

    latest_reflect = pipeline.get("latest_reflect_run") or {}
    if latest_reflect.get("error"):
        alerts.append(
            _alert(
                "reflect_failed",
                "critical",
                "反思任务失败",
                str(latest_reflect.get("error"))[:200],
                "查看 reflect_run 记录与 LLM 端点可用性；可手动 POST /workers/reflect/run 重试。",
                {"run_id": latest_reflect.get("id")},
            )
        )
    elif latest_reflect.get("curate_failed") or latest_reflect.get("rejecter_failed"):
        alerts.append(
            _alert(
                "reflect_degraded",
                "medium",
                "反思任务降级完成",
                "curator 或 rejecter LLM 调用失败，本轮蒸馏结果不完整（宁 miss 不脏写）。",
                "检查 OPENAI_API_KEY / 端点白名单；观察下一轮是否恢复。",
            )
        )

    latest_param = pipeline.get("latest_param_advice_run") or {}
    if latest_param.get("status") == "failed":
        alerts.append(
            _alert(
                "param_advice_failed",
                "high",
                "参数建议任务失败",
                str(latest_param.get("error_code") or "参数建议任务未完成")[:200],
                "检查论文源可达性与端点白名单 ALLOWED_API_HOSTS。",
                {"run_id": latest_param.get("id")},
            )
        )

    if requests.get("count"):
        error_rate = float(requests.get("error_rate") or 0.0)
        if error_rate >= float(settings.MONITOR_ALERT_ERROR_RATE):
            alerts.append(
                _alert(
                    "high_error_rate",
                    "high",
                    "接口错误率偏高",
                    f"最近 {snapshot.get('requests', {}).get('window_seconds')}s 内 5xx 占比 "
                    f"{round(error_rate * 100, 2)}%（{requests.get('errors')}/{requests.get('count')}）。",
                    "查看 /monitor/logs?only_problems=true 定位具体端点与状态码。",
                    {"error_rate": error_rate},
                )
            )
        p95 = float(requests.get("p95_ms") or 0.0)
        if p95 >= float(settings.MONITOR_ALERT_P95_MS):
            alerts.append(
                _alert(
                    "slow_p95",
                    "medium",
                    "接口 p95 延迟偏高",
                    f"p95 = {round(p95, 1)}ms（阈值 {settings.MONITOR_ALERT_P95_MS}ms）。",
                    "检查 LLM/精排端点耗时与 SQLite 锁等待；必要时下调 top_k。",
                    {"p95_ms": p95},
                )
            )

    zero_rate = quality.get("zero_recall_rate")
    if (
        zero_rate is not None
        and quality.get("real", 0) >= 10
        and float(zero_rate) >= float(settings.MONITOR_ALERT_ZERO_RECALL_RATE)
    ):
        alerts.append(
            _alert(
                "high_zero_recall",
                "high",
                "零召回率偏高",
                f"{quality.get('window_days')} 天窗口内 {quality.get('real')} 次真实检索中 "
                f"{quality.get('zero')} 次零命中（{round(float(zero_rate) * 100, 2)}%）。",
                "核对 embedding 端点与 FTS 索引；参见 ADR-0028（拾遗）与 ops-runbook。",
                {"zero_recall_rate": zero_rate},
            )
        )

    backlog = int(pipeline.get("candidates_pending_review") or 0)
    if backlog >= int(settings.MONITOR_ALERT_BACKLOG):
        alerts.append(
            _alert(
                "candidate_backlog",
                "medium",
                "待审候选积压",
                f"pending_review = {backlog}（阈值 {settings.MONITOR_ALERT_BACKLOG}），"
                f"其中 {pipeline.get('candidates_pending_over_24h')} 条超过 24 小时未裁决。",
                "在案牍审阅台批量处置；或调高 CANDIDATE_MIN_CONFIDENCE 收紧入队信噪门。",
                {"pending": backlog},
            )
        )

    db_mb = float(storage.get("database", {}).get("mb") or 0.0)
    if db_mb >= float(settings.MONITOR_ALERT_DB_MB):
        alerts.append(
            _alert(
                "database_size",
                "medium",
                "SQLite 体积偏大",
                f"主库 {db_mb}MB（阈值 {settings.MONITOR_ALERT_DB_MB}MB）。",
                "执行遗忘/沉淀收敛，或用 scripts/backup_restore.py 备份后归档旧库。",
                {"mb": db_mb},
            )
        )

    if security.get("api_key_configured") and not security.get("api_keys_total"):
        alerts.append(
            _alert(
                "auth_dev_fallback",
                "high",
                "鉴权实际未生效（dev 回退放行）",
                "已配置 API_KEY，但 api_keys 表无任何记录：无 Authorization 头的请求会走 "
                "get_current_user 的 dev 回退直接放行；前端发送的 X-API-Key 头当前也没有依赖在校验。",
                "用 lantai.core.auth.create_api_key 签发 Bearer key 并让客户端改用 "
                "Authorization: Bearer <key>；或保持仅回环（127.0.0.1）部署。",
                {"effective_auth": security.get("effective_auth")},
            )
        )

    if not security.get("loopback") and not security.get("api_key_configured"):
        alerts.append(
            _alert(
                "insecure_binding",
                "critical",
                "非回环绑定且无 API Key",
                f"HOST={security.get('host')} 非回环但未配置 API_KEY（启动守卫会拒绝启动）。",
                "设置 API_KEY 或把 HOST 改回 127.0.0.1。",
            )
        )

    if not dependency.get("llm", {}).get("configured"):
        alerts.append(
            _alert(
                "llm_key_missing",
                "medium",
                "未配置 OPENAI_API_KEY",
                "LLM 提取、去重中带兜底与 embedding 将不可用，系统退化为直写 + 本地检索。",
                "在 .env 配置 OPENAI_API_KEY（或兼容端点 + OPENAI_BASE_URL）。",
            )
        )

    alerts.sort(key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 9), item["id"]))
    return alerts


# ── 快照与视图 ─────────────────────────────────────────────────────────
def build_monitor_snapshot(
    session,
    *,
    now: datetime | None = None,
    window_seconds: int | None = None,
    include_quality: bool = True,
    quality_window_days: int | None = None,
) -> dict:
    """一次装配全部监控事实（只读）。`session` 由调用方给出，便于测试直传。

    `include_quality=False` 跳过 `recall_report`（它会按窗口读 retrieval_event，
    事件量大时不便宜）——面板徽标轮询走轻量口径。
    """
    now = now or utcnow()
    window = int(window_seconds or settings.MONITOR_WINDOW_SECONDS)
    collector = metrics_module.get_collector()
    overview = build_overview(session)
    quality: dict = {}
    if include_quality:
        try:
            quality = (
                recall_report(days=quality_window_days) if quality_window_days else recall_report()
            )
        except ValueError:
            quality = {}
    snapshot = {
        "generated_at": now.isoformat(timespec="seconds"),
        "version": _app_version(),
        "process": process_stats().as_dict(),
        "storage": collect_storage_metrics(session),
        "memories": overview["memories"],
        "review": {
            "candidates_pending_review": overview["candidates_pending_review"],
            "proposals_pending": overview["proposals_pending"],
            "checkpoints": overview["checkpoints"],
            "provenance_by_prompt": overview["provenance_by_prompt"],
        },
        "pipeline": collect_pipeline_metrics(session, now=now),
        "scheduler": collect_scheduler_metrics(session, now=now),
        "requests": collector.snapshot(window, now=now.timestamp()),
        "quality": quality,
        "security": collect_security_view(session),
        "dependency": collect_dependency_view(),
    }
    snapshot["alerts"] = evaluate_alerts(snapshot)
    snapshot["summary"] = {
        "status": (
            "critical"
            if any(a["severity"] == "critical" for a in snapshot["alerts"])
            else "degraded"
            if snapshot["alerts"]
            else "healthy"
        ),
        "alert_count": len(snapshot["alerts"]),
        "critical_count": sum(1 for a in snapshot["alerts"] if a["severity"] == "critical"),
        "requests_total": snapshot["requests"]["totals"]["requests_total"],
        "uptime_seconds": snapshot["process"]["uptime_seconds"],
        "memories_total": overview["memories"]["total"],
    }
    return snapshot


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("lantai")
    except Exception:
        return "unknown"


def get_monitor_snapshot(
    *,
    window_seconds: int | None = None,
    include_quality: bool = True,
    quality_window_days: int | None = None,
) -> dict:
    """打开默认会话装配快照（路由入口）。"""
    with db.get_session() as s:
        return build_monitor_snapshot(
            s,
            window_seconds=window_seconds,
            include_quality=include_quality,
            quality_window_days=quality_window_days,
        )


def monitor_series(minutes: int | None = None) -> list[dict]:
    """分钟级请求趋势（缺分钟补零）。"""
    if minutes is not None and (
        not isinstance(minutes, int) or isinstance(minutes, bool) or not (1 <= minutes <= 1440)
    ):
        raise ValueError("minutes must be an int in [1, 1440]")
    return metrics_module.get_collector().series(minutes)


def list_operation_logs(limit: int = 100, *, only_problems: bool = False) -> list[dict]:
    """`operation_logs` 落库记录（新→旧）；`only_problems` 只看错误与慢请求。"""
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    statement = select(OperationLog).order_by(OperationLog.created_at.desc()).limit(limit)
    with db.get_session() as s:
        rows = s.exec(statement).all()
    slow_ms = float(settings.MONITOR_PERSIST_SLOW_MS)
    items = []
    for row in rows:
        problem = row.status_code >= 400 or float(row.latency_ms or 0.0) >= slow_ms
        if only_problems and not problem:
            continue
        items.append(
            {
                "id": row.id,
                "endpoint": row.endpoint,
                "user_id": row.user_id,
                "status_code": row.status_code,
                "latency_ms": round(float(row.latency_ms or 0.0), 3),
                "problem": problem,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return items


_SENSITIVE_HINTS = ("key", "token", "secret", "password", "credential")
_SETTING_GROUPS = {
    "运行": (
        "HOST",
        "PORT",
        "LANTAI_RUN_SCHEDULER",
        "LANTAI_HOME",
        "DATABASE_URL",
        "API_KEY",
        "OPENAI_API_KEY",
        "RERANKER_API_KEY",
    ),
    "调度": (
        "INGEST_CRON_MINUTES",
        "EVOLVE_CRON_MINUTES",
        "FORGET_CRON_HOURS",
        "CANDIDATE_TTL_CRON_HOURS",
        "DIGEST_CRON_HOUR",
        "REFLECT_CRON_HOUR",
        "AUTODREAM_CRON_DAYS",
        "PARAM_ADVICE_CRON_MINUTES",
    ),
    "检索": (
        "RETRIEVAL_W_VECTOR",
        "RETRIEVAL_W_BM25",
        "RETRIEVAL_W_FTS",
        "RETRIEVAL_W_DECAY",
        "RERANKER_ENABLED",
        "FTS_RECALL_TOP_K",
        "GATE_CACHE_TTL",
        "GATE_MIN_EXTRACTOR_CONF",
    ),
    "遗忘": ("ARCHIVE_DECAY_THRESHOLD", "WORKING_MEMORY_TTL_DAYS", "LANE_DECAY_PROFILES"),
    "去重": (
        "DEDUP_MERGE_THRESHOLD",
        "DEDUP_UPDATE_THRESHOLD",
        "DEDUP_PRESCREEN_MERGE",
        "DEDUP_STRUCTURAL_ENABLED",
    ),
    "监控": (
        "MONITOR_ENABLED",
        "MONITOR_WINDOW_SECONDS",
        "MONITOR_PERSIST_SLOW_MS",
        "MONITOR_PERSIST_SAMPLE",
        "MONITOR_RETENTION_DAYS",
        "MONITOR_ALERT_ERROR_RATE",
        "MONITOR_ALERT_P95_MS",
        "MONITOR_ALERT_ZERO_RECALL_RATE",
        "MONITOR_ALERT_BACKLOG",
        "MONITOR_ALERT_DB_MB",
    ),
}


def safe_settings_view() -> dict:
    """运行配置只读视图（密钥类一律打码，绝不回传明文）。"""
    view: dict[str, dict] = {}
    for group, names in _SETTING_GROUPS.items():
        bucket: dict[str, object] = {}
        for name in names:
            if not hasattr(settings, name):
                continue
            value = getattr(settings, name)
            if any(hint in name.lower() for hint in _SENSITIVE_HINTS):
                bucket[name] = "••••••" if value else ""
            elif name == "DATABASE_URL":
                bucket[name] = _mask_path(str(value))
            else:
                bucket[name] = value
        view[group] = bucket
    return view


def _mask_path(value: str) -> str:
    """数据库 URL 只留文件名，避免把宿主目录结构泄给前端。"""
    if not value:
        return value
    return f"sqlite:///{Path(value.removeprefix('sqlite:///')).name}"


# ── Prometheus 文本视图 ────────────────────────────────────────────────
def render_prometheus(snapshot: dict) -> str:
    """把快照渲染成 Prometheus 文本格式（零依赖，便于外部监控抓取）。"""
    lines: list[str] = []

    def gauge(name: str, help_text: str, value, labels: dict | None = None) -> None:
        if value is None:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{_labels(labels)} {_format(value)}")

    def series(name: str, help_text: str, rows: list[tuple[dict, object]]) -> None:
        if not rows:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in rows:
            if value is None:
                continue
            lines.append(f"{name}{_labels(labels)} {_format(value)}")

    summary = snapshot.get("summary", {})
    gauge("lantai_up", "Whether the Lantai process is serving.", 1)
    gauge(
        "lantai_healthy",
        "1 = no alerts, 0 = alerts present.",
        1 if summary.get("status") == "healthy" else 0,
    )
    series(
        "lantai_alerts",
        "Active alert count by severity.",
        [
            ({"severity": sev}, sum(1 for a in snapshot.get("alerts", []) if a["severity"] == sev))
            for sev in ("critical", "high", "medium", "info")
        ],
    )
    gauge(
        "lantai_process_uptime_seconds",
        "Process uptime in seconds.",
        snapshot.get("process", {}).get("uptime_seconds"),
    )
    gauge(
        "lantai_process_rss_bytes",
        "Resident set size in bytes.",
        round(float(snapshot.get("process", {}).get("rss_mb") or 0.0) * 1024 * 1024),
    )
    gauge(
        "lantai_process_threads", "Active thread count.", snapshot.get("process", {}).get("threads")
    )

    memories = snapshot.get("memories", {})
    gauge("lantai_memories_total", "Total memory rows.", memories.get("total"))
    gauge("lantai_memories_active", "Active memory rows.", memories.get("active"))
    gauge("lantai_memories_archived", "Archived memory rows.", memories.get("archived"))
    series(
        "lantai_memories_by_lane",
        "Memory rows by lane.",
        [({"lane": lane}, count) for lane, count in (memories.get("by_lane") or {}).items()],
    )
    series(
        "lantai_memories_by_decay_class",
        "Memory rows by decay class.",
        [
            ({"decay_class": cls}, count)
            for cls, count in (memories.get("by_decay_class") or {}).items()
        ],
    )

    review = snapshot.get("review", {})
    gauge(
        "lantai_candidates_pending_review",
        "Candidates awaiting human review.",
        review.get("candidates_pending_review"),
    )
    gauge(
        "lantai_proposals_pending", "Proposals awaiting decision.", review.get("proposals_pending")
    )
    gauge(
        "lantai_conflicts_open",
        "Open conflict ledger entries.",
        snapshot.get("pipeline", {}).get("conflicts_open"),
    )

    requests = snapshot.get("requests", {})
    window = requests.get("window", {})
    totals = requests.get("totals", {})
    gauge(
        "lantai_http_requests_total",
        "Requests served since process start.",
        totals.get("requests_total"),
    )
    gauge(
        "lantai_http_server_errors_total",
        "5xx responses since process start.",
        totals.get("server_errors_total"),
    )
    gauge(
        "lantai_http_requests_per_minute",
        "Request rate in the monitor window.",
        window.get("requests_per_minute"),
    )
    gauge("lantai_http_error_rate", "5xx ratio in the monitor window.", window.get("error_rate"))
    gauge(
        "lantai_http_request_duration_ms_p95",
        "p95 latency (ms) in the monitor window.",
        window.get("p95_ms"),
    )
    gauge(
        "lantai_http_request_duration_ms_avg",
        "Mean latency (ms) in the monitor window.",
        window.get("avg_ms"),
    )
    series(
        "lantai_http_requests_by_route",
        "Requests per route in the monitor window.",
        [({"route": row["route"]}, row["count"]) for row in requests.get("endpoints", [])],
    )
    series(
        "lantai_http_request_duration_ms_p95_by_route",
        "p95 latency (ms) per route in the monitor window.",
        [({"route": row["route"]}, row["p95_ms"]) for row in requests.get("endpoints", [])],
    )

    storage = snapshot.get("storage", {})
    gauge(
        "lantai_database_bytes",
        "SQLite main database size in bytes.",
        storage.get("database", {}).get("bytes"),
    )
    gauge(
        "lantai_database_wal_bytes",
        "SQLite WAL size in bytes.",
        storage.get("database", {}).get("wal_bytes"),
    )
    gauge(
        "lantai_vector_store_bytes",
        "ChromaDB directory size in bytes.",
        storage.get("vector_store", {}).get("bytes"),
    )
    series(
        "lantai_table_rows",
        "Row count of core tables.",
        [({"table": table}, count) for table, count in (storage.get("table_rows") or {}).items()],
    )

    scheduler_view = snapshot.get("scheduler", {})
    gauge(
        "lantai_scheduler_running",
        "1 = APScheduler running.",
        1 if scheduler_view.get("running") else 0,
    )
    gauge("lantai_scheduler_jobs", "Registered scheduler jobs.", scheduler_view.get("job_count"))
    series(
        "lantai_worker_overdue",
        "1 = worker overdue against its schedule.",
        [
            ({"worker": row["name"]}, 1 if row.get("overdue") else 0)
            for row in scheduler_view.get("workers", [])
        ],
    )
    series(
        "lantai_worker_last_run_timestamp_seconds",
        "Unix timestamp of the last successful worker run.",
        [
            ({"worker": row["name"]}, _epoch(row.get("last_run")))
            for row in scheduler_view.get("workers", [])
        ],
    )

    quality = snapshot.get("quality", {})
    gauge(
        "lantai_zero_recall_rate",
        "Zero-result ratio over the monitor window.",
        quality.get("zero_recall_rate"),
    )
    gauge(
        "lantai_retrieval_events_total",
        "Retrieval events in the quality window.",
        quality.get("real"),
    )

    lines.append("")
    return "\n".join(lines)


def _labels(labels: dict | None) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{key}="{_escape(str(value))}"' for key, value in labels.items())
    return "{" + parts + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.6g}"


def _epoch(value: str | None) -> float | None:
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else None
