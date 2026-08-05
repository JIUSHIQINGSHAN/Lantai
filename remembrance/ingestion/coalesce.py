"""Tidal Coalescing — 短消息异步缓冲合并，减少 LLM 提取调用"""
import hashlib
import time
import threading

from remembrance.core.settings import settings


class CoalesceBuffer:
    """按 user_id + lane 分键的消息缓冲器。

    冲刷触发条件（任意满足即冲刷）：
    - idle_timeout: 最后一条消息后空闲超时
    - window: 从第一条消息起的时间窗口
    - max_parts: 消息条数上限
    - max_chars: 总字符数上限

    v0.4：add_async 幂等入队（_seen 记录 content 指纹，TTL 内重复返回同一
    job_id）；合并统计（_flush_count/_last_flush_ts）由 water_level() 返回。
    """

    # 幂等去重窗口（秒）：同内容在此窗口内重复 add_async 只入队一次
    IDEMPOTENT_TTL = 3600.0

    def __init__(self):
        self._buffers: dict[str, list[dict]] = {}
        self._timestamps: dict[str, float] = {}
        self._first_msg: dict[str, float] = {}
        self._seen: dict[str, float] = {}  # content 指纹 → 首次入队时间
        self._flush_count = 0
        self._last_flush_ts: float | None = None
        self._lock = threading.Lock()

    def _key(self, user_id: str, lane: str) -> str:
        return f"{user_id}:{lane}"

    def _profile(self, lane: str) -> dict:
        return settings.LANE_COALESCE_PROFILES.get(
            lane, settings.LANE_COALESCE_PROFILES["general"]
        )

    @staticmethod
    def job_id(user_id: str, lane: str, content: str) -> str:
        return hashlib.sha256(f"{user_id}:{lane}:{content}".encode()).hexdigest()[:16]

    def add(self, user_id: str, lane: str, content: str, title: str = "") -> dict:
        """将消息加入缓冲。返回 {'buffered': True, 'count': N} 或 {'flushed': [...]}"""
        key = self._key(user_id, lane)
        now = time.time()
        profile = self._profile(lane)

        with self._lock:
            if key not in self._buffers:
                self._buffers[key] = []
                self._first_msg[key] = now
            self._buffers[key].append({
                "content": content, "title": title, "ts": now,
            })
            self._timestamps[key] = now

            buf = self._buffers[key]
            total_chars = sum(len(m["content"]) for m in buf)

            should_flush = (
                len(buf) >= profile["max_parts"]
                or total_chars >= profile["max_chars"]
                or (now - self._first_msg[key]) >= profile["window"]
            )

            if should_flush:
                return self._flush(key)

            count = len(buf)
        # 锁内算好返回值，避免并发下读到已冲刷的空表
        return {"buffered": True, "count": count}

    def add_async(self, user_id: str, lane: str, content: str,
                  title: str = "") -> dict:
        """异步入队（幂等）：TTL 内相同内容返回同一 job_id，不重复入队。"""
        jid = self.job_id(user_id, lane, content)
        now = time.time()
        with self._lock:
            prev = self._seen.get(jid)
            if prev is not None and (now - prev) < self.IDEMPOTENT_TTL:
                return {"status": "queued", "job_id": jid, "duplicate": True}
            self._seen[jid] = now
        result = self.add(user_id, lane, content, title)
        if result.get("buffered"):
            return {"status": "queued", "job_id": jid}
        return {"status": "flushed", "job_id": jid, "detail": result}

    def forget_fingerprint(self, job_id: str) -> None:
        """删除幂等指纹（持久化失败时调用，允许重试）。"""
        with self._lock:
            self._seen.pop(job_id, None)

    def check_idle(self) -> list[dict]:
        """检查空闲超时的缓冲并冲刷。由 worker 定期调用。"""
        flushed = []
        now = time.time()
        with self._lock:
            # 顺带清理过期幂等指纹，防止长跑进程内存单调增长
            stale = [k for k, ts in self._seen.items()
                     if (now - ts) >= self.IDEMPOTENT_TTL]
            for k in stale:
                del self._seen[k]
            for key in list(self._buffers.keys()):
                profile = self._profile(key.split(":")[-1])
                if (now - self._timestamps[key]) >= profile["idle_timeout"]:
                    result = self._flush(key)
                    if isinstance(result, dict) and "items" in result:
                        flushed.append(result)
        return flushed

    def _flush(self, key: str) -> dict:
        """冲刷指定缓冲键。"""
        messages = self._buffers.pop(key, [])
        self._timestamps.pop(key, None)
        self._first_msg.pop(key, None)
        if not messages:
            return {"flushed": False}
        combined = "\n".join(m["content"] for m in messages)
        self._flush_count += 1
        self._last_flush_ts = time.time()
        return {
            "flushed": True,
            "key": key,
            "count": len(messages),
            "combined_content": combined,
            "items": messages,
        }

    def water_level(self) -> dict:
        """返回当前缓冲水位与合并统计（用于 /stats）。"""
        with self._lock:
            return {
                "active_keys": len(self._buffers),
                "total_messages": sum(len(v) for v in self._buffers.values()),
                "seen_fingerprints": len(self._seen),
                "flush_count": self._flush_count,
                "last_flush_ts": self._last_flush_ts,
            }


# 全局单例
_buffer = CoalesceBuffer()


def get_coalesce_buffer() -> CoalesceBuffer:
    return _buffer
