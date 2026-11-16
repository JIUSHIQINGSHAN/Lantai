"""APScheduler 3 的异常边界与只读活性快照。

只用于启动时重注册全部作业的内存 jobstore。持久 store 在 submit 后 update
失败会重复分发；外围重试不能给持久 store 提供 exactly-once，不能直接换回它。
"""

import time
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED, STATE_STOPPED

from lantai.core.logger import logger
from lantai.core.time import iso_utc


class RecoveringScheduler(BackgroundScheduler):
    # 调度元数据均在内存：每 5s 最多检查一轮，以完整循环进展探测锁死/反复异常。
    # 60s 宽限容纳 1/2/4/8/16/30s 退避及暂时线程饥饿；长于宽限即 health 失败。
    LOOP_HEARTBEAT_SECONDS = 5.0
    LOOP_STALE_SECONDS = 60.0
    RETRY_INITIAL_SECONDS = 1.0
    RETRY_MAX_SECONDS = 30.0
    # 异常 trigger 返回零等待也不得 CPU 忙转；正常高频任务最小调度精度为 50ms。
    MIN_LOOP_WAIT_SECONDS = 0.05

    def start(self, *args, **kwargs):
        self._loop_state = {
            "started_monotonic": time.monotonic(),
            "last_tick_at": None,
            "last_progress_at": None,
            "last_progress_monotonic": None,
            "error_count": 0,
            "consecutive_errors": 0,
            "last_error": None,
            "last_error_at": None,
            "retry_delay_seconds": 0.0,
        }
        super().start(*args, **kwargs)

    def _main_loop(self):
        wait_seconds = 0.0
        retry_at = 0.0
        while self.state != STATE_STOPPED:
            self._event.wait(wait_seconds)
            self._event.clear()
            if self.state == STATE_STOPPED:
                break
            # add_job/wakeup 不得绕过失败退避；shutdown 则在上面立即生效。
            remaining = retry_at - time.monotonic()
            if remaining > 0:
                wait_seconds = remaining
                continue
            self._loop_state = {
                **self._loop_state,
                "last_tick_at": iso_utc(datetime.now(UTC)),
            }
            try:
                delay = self._process_jobs()
            except Exception as exc:
                previous = self._loop_state
                backoff = min(
                    self.RETRY_MAX_SECONDS,
                    max(self.RETRY_INITIAL_SECONDS, previous["retry_delay_seconds"] * 2),
                )
                self._loop_state = {
                    **previous,
                    "error_count": previous["error_count"] + 1,
                    "consecutive_errors": previous["consecutive_errors"] + 1,
                    # 状态面只给类型，SQL/路径等详情仅留服务日志。
                    "last_error": type(exc).__name__,
                    "last_error_at": iso_utc(datetime.now(UTC)),
                    "retry_delay_seconds": backoff,
                }
                logger.exception(
                    "Scheduler loop failed (%s); retry in %.2fs", type(exc).__name__, backoff
                )
                retry_at = time.monotonic() + backoff
                wait_seconds = backoff
            else:
                previous = self._loop_state
                if previous["consecutive_errors"]:
                    logger.info(
                        "Scheduler loop recovered after %d failures", previous["consecutive_errors"]
                    )
                self._loop_state = {
                    **previous,
                    "last_progress_at": iso_utc(datetime.now(UTC)),
                    "last_progress_monotonic": time.monotonic(),
                    "consecutive_errors": 0,
                    "retry_delay_seconds": 0.0,
                }
                retry_at = 0.0
                wait_seconds = max(
                    self.MIN_LOOP_WAIT_SECONDS,
                    min(self.LOOP_HEARTBEAT_SECONDS, delay)
                    if delay is not None
                    else self.LOOP_HEARTBEAT_SECONDS,
                )

    def loop_status(self) -> dict:
        """不拿 jobstore 锁/不做 I/O；线程锁死时探针仍能读最近完整循环。

        快照用整个 dict 替换发布，读侧只取一次引用，避免拼出半次写入的数据。
        年龄用 monotonic（不受校时影响），UTC 时间戳仅供运维对时。
        """
        state = getattr(self, "_loop_state", {})
        thread = getattr(self, "_thread", None)
        alive = bool(thread is not None and thread.is_alive())
        baseline = state.get("last_progress_monotonic")
        if baseline is None:
            baseline = state.get("started_monotonic")
        age = max(0.0, time.monotonic() - baseline) if baseline is not None else None
        if not self.running:
            reason = "stopped"
        elif not alive:
            reason = "thread_dead"
        elif self.state == STATE_PAUSED:
            reason = "paused"
        elif age is None or age > self.LOOP_STALE_SECONDS:
            reason = "stalled"
        else:
            reason = "recovering" if state.get("consecutive_errors") else "ok"
        return {
            "running": bool(self.running and alive),
            "thread_alive": alive,
            "healthy": reason in ("ok", "recovering"),
            "reason": reason,
            "last_tick_at": state.get("last_tick_at"),
            "last_progress_at": state.get("last_progress_at"),
            "progress_age_seconds": round(age, 3) if age is not None else None,
            "stale_after_seconds": self.LOOP_STALE_SECONDS,
            "error_count": state.get("error_count", 0),
            "consecutive_errors": state.get("consecutive_errors", 0),
            "last_error": state.get("last_error"),
            "last_error_at": state.get("last_error_at"),
            "retry_delay_seconds": state.get("retry_delay_seconds", 0.0),
        }
