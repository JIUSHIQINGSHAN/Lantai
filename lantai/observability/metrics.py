"""司天（ADR-0044）：进程内运行指标采集器——零第三方依赖、线程安全。

设计约束（与兰台单进程部署假设一致，见 README「部署约束」）：

- **只读内存态**：请求级指标不落库；落库由 `telemetry.py` 采样批量执行
  （只留错误 / 慢请求 / 1-N 采样），杜绝每请求一次 SQLite 写入的写放大。
- **双视图共用一次记录**：最近 N 条请求环形缓冲（分位数、端点排行、最近错误）
  + 分钟级聚合桶（趋势曲线），一次 `record()` 同时喂两边。
- **纯函数可单测**：`normalize_route` / `percentile` / `aggregate` 不依赖 IO，
  测试直调（AGENTS.md 测试纪律：核心函数必须有不 mock 的冒烟测试）。
- **失败不影响主链路**：`record()` 全程不抛异常（宁 miss 不脏写）。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow

# 进程启动时刻（模块导入即固定）：uptime 与 worker 逾期基线共用
PROCESS_STARTED_AT = utcnow()
PROCESS_STARTED_MONO = time.monotonic()

# 路径中的标识符片段：纯数字 / 长十六进制 / ULID（可带 mem_ 等前缀）
_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-f]{8,}|[0-9A-HJKMNP-TV-Z]{26}|[a-z]+_(?:\d+|[0-9A-HJKMNP-TV-Z]{26}))$"
)


def normalize_route(path: str, route_path: str | None = None) -> str:
    """把具体 URL 归一成路由模板，避免高基数打散统计。

    优先用框架给出的路由模板（Starlette `route.path_format`，如 `/memory/{memory_id}`）；
    拿不到时按片段特征把标识符替换成 `{id}`（`/memory/mem_01J8...` → `/memory/{id}`）。
    """
    if route_path:
        return route_path
    raw = path or "/"
    parts = []
    for seg in raw.split("/"):
        parts.append("{id}" if seg and _ID_SEGMENT.match(seg) else seg)
    normalized = "/".join(parts)
    return normalized or "/"


def percentile(values: list[float], q: float) -> float:
    """线性插值分位数（q ∈ [0, 1]）；空集返回 0.0。纯函数。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 3)
    ratio = min(max(float(q), 0.0), 1.0)
    idx = (len(ordered) - 1) * ratio
    low = int(idx)
    high = min(low + 1, len(ordered) - 1)
    frac = idx - low
    return round(float(ordered[low]) * (1 - frac) + float(ordered[high]) * frac, 3)


@dataclass(frozen=True)
class RequestRecord:
    """一次 HTTP 请求的运行指标（内存态，只读）。"""

    ts: float                      # time.time()（与 created_at 对齐）
    method: str
    route: str
    status: int
    latency_ms: float
    user_id: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Bucket:
    """分钟级聚合桶（趋势曲线用）：只留计数与延迟和，不留原始样本。"""

    minute: int
    count: int = 0
    errors: int = 0
    latency_sum: float = 0.0
    latency_max: float = 0.0


def aggregate(records: list[RequestRecord], *, elapsed_seconds: float) -> dict:
    """把一批请求记录聚合成窗口统计（纯函数，窗口统计与端点排行共用口径）。"""
    total = len(records)
    if not total:
        return {
            "count": 0, "errors": 0, "client_errors": 0, "error_rate": 0.0,
            "requests_per_minute": 0.0, "avg_ms": 0.0,
            "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0,
            "status_classes": {},
        }
    latencies = [r.latency_ms for r in records]
    server_errors = sum(1 for r in records if r.status >= 500)
    client_errors = sum(1 for r in records if 400 <= r.status < 500)
    minutes = max(elapsed_seconds, 1.0) / 60.0
    classes: dict[str, int] = {}
    for r in records:
        classes[f"{r.status // 100}xx"] = classes.get(f"{r.status // 100}xx", 0) + 1
    return {
        "count": total,
        "errors": server_errors,
        "client_errors": client_errors,
        "error_rate": round(server_errors / total, 4),
        "requests_per_minute": round(total / minutes, 3),
        "avg_ms": round(sum(latencies) / total, 3),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "max_ms": round(max(latencies), 3),
        "status_classes": classes,
    }


class MetricsCollector:
    """线程安全的进程内指标收集器（单进程部署下的事实源）。"""

    def __init__(self, *, buffer: int | None = None, bucket_minutes: int | None = None):
        self._buffer_max = max(1, int(buffer or settings.MONITOR_REQUEST_BUFFER))
        self._bucket_max = max(5, int(bucket_minutes or settings.MONITOR_BUCKET_MINUTES))
        self._lock = threading.Lock()
        self._records: deque[RequestRecord] = deque(maxlen=self._buffer_max)
        self._buckets: dict[int, _Bucket] = {}
        self._total = 0
        self._server_errors = 0

    # ── 写入 ──────────────────────────────────────────────────────────
    def record(self, *, method: str, route: str, status: int,
               latency_ms: float, user_id: str = "", ts: float | None = None) -> None:
        """记录一次请求。任何异常只记日志（宁 miss 不脏写）。"""
        try:
            stamp = float(ts if ts is not None else time.time())
            latency = max(0.0, float(latency_ms))
            record = RequestRecord(ts=stamp, method=(method or "GET").upper(),
                                   route=route or "/", status=int(status),
                                   latency_ms=round(latency, 3), user_id=user_id or "")
            minute = int(stamp // 60)
            with self._lock:
                self._total += 1
                if record.status >= 500:
                    self._server_errors += 1
                self._records.append(record)
                bucket = self._buckets.get(minute)
                if bucket is None:
                    bucket = _Bucket(minute=minute)
                    self._buckets[minute] = bucket
                bucket.count += 1
                if record.status >= 500:
                    bucket.errors += 1
                bucket.latency_sum += record.latency_ms
                bucket.latency_max = max(bucket.latency_max, record.latency_ms)
                self._prune_buckets(minute)
        except Exception:  # 采集失败绝不影响请求主链路
            logger.exception("metrics record failed (non-fatal)")

    def _prune_buckets(self, current_minute: int) -> None:
        floor = current_minute - self._bucket_max
        stale = [m for m in self._buckets if m < floor]
        for m in stale:
            self._buckets.pop(m, None)

    # ── 读取 ──────────────────────────────────────────────────────────
    def _window_records(self, seconds: float, now: float | None = None) -> list[RequestRecord]:
        floor = (now if now is not None else time.time()) - max(1.0, float(seconds))
        with self._lock:
            return [r for r in self._records if r.ts >= floor]

    def totals(self) -> dict:
        """自进程启动以来的累计计数。"""
        with self._lock:
            return {
                "requests_total": self._total,
                "server_errors_total": self._server_errors,
                "buffer_size": len(self._records),
                "buffer_capacity": self._buffer_max,
                # 环形缓冲淘汰掉的旧请求数（窗口统计之外仍可从 operation_logs 取回）
                "evicted_total": max(0, self._total - len(self._records)),
            }

    def window(self, seconds: float, now: float | None = None) -> dict:
        """最近 N 秒窗口统计（错误率 / 分位数 / 吞吐）。"""
        records = self._window_records(seconds, now)
        return aggregate(records, elapsed_seconds=seconds)

    def endpoints(self, seconds: float, limit: int = 20,
                  now: float | None = None) -> list[dict]:
        """窗口内按路由聚合的耗时排行（按请求数降序，其次按 p95 降序）。"""
        records = self._window_records(seconds, now)
        grouped: dict[str, list[RequestRecord]] = {}
        for r in records:
            grouped.setdefault(r.route, []).append(r)
        rows = []
        for route, items in grouped.items():
            stat = aggregate(items, elapsed_seconds=seconds)
            rows.append({
                "route": route,
                "count": stat["count"],
                "errors": stat["errors"],
                "client_errors": stat["client_errors"],
                "avg_ms": stat["avg_ms"],
                "p95_ms": stat["p95_ms"],
                "max_ms": stat["max_ms"],
            })
        rows.sort(key=lambda row: (-row["count"], -row["p95_ms"], row["route"]))
        return rows[:max(1, int(limit))]

    def recent(self, limit: int = 50, *, only_problems: bool = False) -> list[dict]:
        """最近请求（新→旧），可只看错误/慢请求。"""
        slow_ms = float(settings.MONITOR_PERSIST_SLOW_MS)
        with self._lock:
            items = list(self._records)[::-1]
        picked = []
        for r in items:
            if only_problems and not (r.status >= 400 or r.latency_ms >= slow_ms):
                continue
            picked.append({**r.as_dict(), "problem": r.status >= 400 or r.latency_ms >= slow_ms})
            if len(picked) >= max(1, int(limit)):
                break
        return picked

    def series(self, minutes: int | None = None, now: float | None = None) -> list[dict]:
        """分钟级趋势（缺分钟补零）：吞吐 / 错误 / 平均延迟 / 峰值延迟。"""
        span = max(1, int(minutes or settings.MONITOR_SERIES_MINUTES))
        current = int((now if now is not None else time.time()) // 60)
        with self._lock:
            buckets = {m: b for m, b in self._buckets.items()}
        rows = []
        for offset in range(span - 1, -1, -1):
            minute = current - offset
            bucket = buckets.get(minute)
            count = bucket.count if bucket else 0
            latency_sum = bucket.latency_sum if bucket else 0.0
            rows.append({
                "minute": minute,
                "ts": minute * 60,
                "count": count,
                "errors": bucket.errors if bucket else 0,
                "avg_ms": round(latency_sum / count, 3) if count else 0.0,
                "max_ms": round(bucket.latency_max, 3) if bucket else 0.0,
            })
        return rows

    def snapshot(self, seconds: float | None = None, now: float | None = None) -> dict:
        """监控面板「请求」分区一次性快照。"""
        window_seconds = float(seconds or settings.MONITOR_WINDOW_SECONDS)
        return {
            "window_seconds": window_seconds,
            "totals": self.totals(),
            "window": self.window(window_seconds, now),
            "endpoints": self.endpoints(window_seconds, limit=20, now=now),
            "recent_problems": self.recent(20, only_problems=True),
            "process_started_at": PROCESS_STARTED_AT.isoformat(),
        }

    def reset(self) -> None:
        """清空内存态（测试隔离用）。"""
        with self._lock:
            self._records.clear()
            self._buckets.clear()
            self._total = 0
            self._server_errors = 0


_collector: MetricsCollector | None = None
_collector_lock = threading.Lock()


def get_collector() -> MetricsCollector:
    """全局收集器单例（懒初始化，便于测试替换）。"""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = MetricsCollector()
    return _collector


def set_collector(collector: MetricsCollector | None) -> None:
    """替换/清空全局收集器（测试用）。"""
    global _collector
    with _collector_lock:
        _collector = collector


def reset_metrics() -> None:
    """清空全局收集器内存态（测试用）。"""
    get_collector().reset()


def is_excluded(path: str) -> bool:
    """静态资源等噪音路径不计入遥测（前缀匹配，配置驱动）。"""
    if not path:
        return False
    return any(path.startswith(prefix) for prefix in settings.MONITOR_EXCLUDE_PATHS)


@dataclass
class ProcessStats:
    """进程运行指标（零第三方依赖，psutil 可选增强）。"""

    pid: int
    uptime_seconds: float
    threads: int
    rss_mb: float
    cpu_seconds: float
    python: str
    open_fds: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _rss_bytes() -> tuple[float, str]:
    """当前 RSS（字节）与来源；Linux 读 /proc/self/status，其余退到 getrusage 峰值。"""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) * 1024.0, "proc_status"
    except OSError:
        pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux 单位 KB，macOS 单位 B——按量级判定，宁粗估不炸
        return (float(peak) * 1024.0 if peak > 1 << 22 else float(peak)), "rusage_peak"
    except Exception:
        return 0.0, "unavailable"


def _open_fds() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


def process_stats() -> ProcessStats:
    """进程级运行指标（uptime / 线程 / RSS / CPU / fd）。"""
    rss, source = _rss_bytes()
    return ProcessStats(
        pid=os.getpid(),
        uptime_seconds=round(time.monotonic() - PROCESS_STARTED_MONO, 3),
        threads=threading.active_count(),
        rss_mb=round(rss / (1024 * 1024), 2),
        cpu_seconds=round(time.process_time(), 3),
        python=sys.version.split()[0],
        open_fds=_open_fds(),
        extra={"rss_source": source},
    )
