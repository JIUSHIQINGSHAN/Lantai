"""Tidal Coalescing — 短消息异步缓冲合并，减少 LLM 提取调用"""
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
    """

    def __init__(self):
        self._buffers: dict[str, list[dict]] = {}
        self._timestamps: dict[str, float] = {}
        self._first_msg: dict[str, float] = {}
        self._lock = threading.Lock()

    def _key(self, user_id: str, lane: str) -> str:
        return f"{user_id}:{lane}"

    def _profile(self, lane: str) -> dict:
        return settings.LANE_COALESCE_PROFILES.get(
            lane, settings.LANE_COALESCE_PROFILES["general"]
        )

    def add(self, user_id: str, lane: str, content: str, title: str = "") -> dict:
        """将消息加入缓冲。返回 {'buffered': True} 或 {'flushed': [...]}"""
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

        return {"buffered": True, "count": len(self._buffers.get(key, []))}

    def check_idle(self) -> list[dict]:
        """检查空闲超时的缓冲并冲刷。由 worker 定期调用。"""
        flushed = []
        now = time.time()
        with self._lock:
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
        return {
            "flushed": True,
            "key": key,
            "count": len(messages),
            "combined_content": combined,
            "items": messages,
        }

    def water_level(self) -> dict:
        """返回当前缓冲水位（用于 /stats）。"""
        with self._lock:
            return {
                "active_keys": len(self._buffers),
                "total_messages": sum(len(v) for v in self._buffers.values()),
            }


# 全局单例
_buffer = CoalesceBuffer()


def get_coalesce_buffer() -> CoalesceBuffer:
    return _buffer
