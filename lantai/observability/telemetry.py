"""司天（ADR-0044）：请求遥测中间件 + `operation_logs` 采样落库。

补齐 ADR-0040 第 3 条（Telemetry & Rate Limiting）留下的空表：
`OperationLog` 自 F13 建表以来从未被写入，运维只能靠 `/stats` 的粗聚合。

落库策略（避免每请求一次 SQLite 写放大）：

- 5xx / 4xx / 超过 `MONITOR_PERSIST_SLOW_MS` 的请求 **必留**（取证优先）；
- 其余按 `MONITOR_PERSIST_SAMPLE`（1/N）采样，N=0 表示只留问题请求；
- 缓冲区由后台线程每 `MONITOR_FLUSH_SECONDS` 批量写入，顺带按
  `MONITOR_RETENTION_DAYS` 清理过期行；
- 任何异常只记日志，绝不冒泡到请求链路（宁 miss 不脏写）。
"""

from __future__ import annotations

import threading
import time

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.observability.metrics import get_collector, is_excluded, normalize_route
from lantai.storage import db

# 未匹配路由的 404 在内存指标中的归并桶名
UNMATCHED_ROUTE = "(unmatched 404)"


def should_persist(
    status: int,
    latency_ms: float,
    *,
    seen: int,
    slow_ms: float | None = None,
    sample: int | None = None,
) -> bool:
    """落库采样判定（纯函数，可单测）：错误/慢请求必留，其余 1/N 采样。"""
    slow = float(settings.MONITOR_PERSIST_SLOW_MS if slow_ms is None else slow_ms)
    rate = int(settings.MONITOR_PERSIST_SAMPLE if sample is None else sample)
    if status >= 400:
        return True
    if latency_ms >= slow:
        return True
    return rate > 0 and seen % rate == 0


class TelemetryWriter:
    """批量落库器：内存缓冲 + 定时 flush + 过期清理。"""

    def __init__(self, *, flush_seconds: float | None = None):
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self._seen = 0
        self._written = 0
        self._failed = 0
        self._flush_seconds = float(flush_seconds or settings.MONITOR_FLUSH_SECONDS)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── 写入侧 ────────────────────────────────────────────────────────
    def offer(
        self, *, method: str, route: str, status: int, latency_ms: float, user_id: str = ""
    ) -> bool:
        """按采样规则决定是否落库；返回 True 表示已入缓冲。"""
        try:
            with self._lock:
                self._seen += 1
                seen = self._seen
                keep = should_persist(status, latency_ms, seen=seen)
                if keep:
                    self._pending.append(
                        {
                            "method": method,
                            "route": route,
                            "status": int(status),
                            "latency_ms": round(float(latency_ms), 3),
                            "user_id": user_id or "",
                        }
                    )
                return keep
        except Exception:
            logger.exception("telemetry offer failed (non-fatal)")
            return False

    # ── 落库侧 ────────────────────────────────────────────────────────
    def flush(self) -> int:
        """把缓冲写入 `operation_logs` 并清理过期行；返回本次写入条数。"""
        with self._lock:
            batch, self._pending = self._pending, []
        if not batch:
            return 0
        try:
            from lantai.models.tables import OperationLog

            now = utcnow()
            with db.get_session() as s:
                for row in batch:
                    s.add(
                        OperationLog(
                            id=new_id("oplog"),
                            user_id=row["user_id"] or "anonymous",
                            endpoint=f"{row['method']} {row['route']}",
                            latency_ms=row["latency_ms"],
                            status_code=row["status"],
                            created_at=now,
                        )
                    )
                s.commit()
            self._written += len(batch)
            self.prune()
            return len(batch)
        except Exception:
            # 落库失败：把这批塞回缓冲（有上限，避免内存无限增长），不阻断服务
            self._failed += len(batch)
            logger.exception("telemetry flush failed (non-fatal)")
            with self._lock:
                if len(self._pending) < 5000:
                    self._pending = batch + self._pending
            return 0

    def prune(self) -> int:
        """清理超过 `MONITOR_RETENTION_DAYS` 的历史行；返回删除条数。"""
        try:
            from datetime import timedelta

            from sqlalchemy import text

            days = max(1, int(settings.MONITOR_RETENTION_DAYS))
            cutoff = (utcnow() - timedelta(days=days)).isoformat(sep=" ")
            with db.get_session() as s:
                result = s.exec(
                    text("DELETE FROM operation_logs WHERE created_at < :cutoff"),
                    params={"cutoff": cutoff},
                )
                s.commit()
                return int(getattr(result, "rowcount", 0) or 0)
        except Exception:
            logger.exception("telemetry prune failed (non-fatal)")
            return 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "pending": len(self._pending),
                "seen": self._seen,
                "written": self._written,
                "failed": self._failed,
                "flush_seconds": self._flush_seconds,
                "sample": int(settings.MONITOR_PERSIST_SAMPLE),
                "slow_ms": float(settings.MONITOR_PERSIST_SLOW_MS),
                "retention_days": int(settings.MONITOR_RETENTION_DAYS),
            }

    # ── 后台线程 ──────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.wait(self._flush_seconds):
            self.flush()

    def start(self) -> None:
        """启动后台落库线程（幂等；`LANTAI_RUN_SCHEDULER=0` 的纯测试进程也可用）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lantai-telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台线程并做最后一次 flush（进程退出前留痕）。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self.flush()


_writer: TelemetryWriter | None = None
_writer_lock = threading.Lock()


def get_writer() -> TelemetryWriter:
    global _writer
    if _writer is None:
        with _writer_lock:
            if _writer is None:
                _writer = TelemetryWriter()
    return _writer


def set_writer(writer: TelemetryWriter | None) -> None:
    """替换/清空全局落库器（测试用）。"""
    global _writer
    with _writer_lock:
        _writer = writer


def flush_telemetry() -> int:
    return get_writer().flush()


class TelemetryMiddleware:
    """纯 ASGI 遥测中间件：请求结束即记录内存指标 + 采样落库。

    不用 `BaseHTTPMiddleware`：它包一层 task/流，对 SSE 与异常路径有额外开销，
    纯 ASGI 包装只做一次计时与一次 `record()`，开销可忽略。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not settings.MONITOR_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if is_excluded(path):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                latency_ms = (time.perf_counter() - started) * 1000.0
                status = status_holder["status"]
                route = _route_template(scope, path)
                user_id = _user_id(scope)
                # 未匹配任何路由的 404（扫描器/错拼路径）在指标里并成一桶，
                # 防止无界路径把端点排行打散；落库仍留真实路径供取证。
                metric_route = (
                    UNMATCHED_ROUTE if scope.get("route") is None and status == 404 else route
                )
                get_collector().record(
                    method=scope.get("method", "GET"),
                    route=metric_route,
                    status=status,
                    latency_ms=latency_ms,
                    user_id=user_id,
                )
                get_writer().offer(
                    method=scope.get("method", "GET"),
                    route=route,
                    status=status,
                    latency_ms=latency_ms,
                    user_id=user_id,
                )
            except Exception:
                logger.exception("telemetry middleware failed (non-fatal)")


def _route_template(scope, path: str) -> str:
    """优先取 Starlette 路由模板，避免 `/memory/{id}` 被具体 ID 打散。"""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return normalize_route(path, template)


def _user_id(scope) -> str:
    """从 `request.state.user_id`（鉴权依赖写入）取调用方；缺失记 anonymous。"""
    state = scope.get("state")
    if isinstance(state, dict):
        return str(state.get("user_id") or "")
    return ""
