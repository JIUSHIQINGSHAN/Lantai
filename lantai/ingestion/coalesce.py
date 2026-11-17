"""Tidal Coalescing — 短消息异步缓冲合并，减少 LLM 提取调用。"""

import hashlib
import json
import threading
import time

from lantai.core.settings import settings


class CoalesceBuffer:
    """Buffer by actor and origin session; retain each received event in a batch.

    Buffering remains process-local. A crash can leave processing events, not a
    fabricated completed outcome. add_async creates an event only after winning
    its existing content retry-window check.
    """

    IDEMPOTENT_TTL = 3600.0

    def __init__(self):
        self._buffers: dict[str, list[dict]] = {}
        self._timestamps: dict[str, float] = {}
        self._first_msg: dict[str, float] = {}
        self._seen: dict[str, float] = {}
        self._seen_event_ids: dict[str, str] = {}
        self._pending_jobs: set[str] = set()
        self._flush_count = 0
        self._last_flush_ts: float | None = None
        self._lock = threading.RLock()

    def _key(self, user_id, lane, session_id="", *, tenant_id=None, agent_id=None):
        # Preserve old ordinary keys for callers that inspect them. Explicit
        # stored fields (not parsing) are authoritative on the new flush path.
        if not tenant_id and not agent_id and ":" not in lane and ":" not in (session_id or ""):
            return f"{user_id}:{lane}:{session_id or ''}"
        return json.dumps([tenant_id, user_id, agent_id, lane, session_id or ""], ensure_ascii=True)

    def _profile(self, lane):
        return settings.LANE_COALESCE_PROFILES.get(lane, settings.LANE_COALESCE_PROFILES["general"])

    @staticmethod
    def job_id(user_id, lane, content, session_id="", *, tenant_id=None, agent_id=None):
        value = json.dumps(
            [tenant_id, user_id, agent_id, lane, session_id or "", content], ensure_ascii=True
        )
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    def add(
        self,
        user_id,
        lane,
        content,
        title="",
        session_id="",
        *,
        event_id=None,
        tenant_id=None,
        agent_id=None,
        content_safety=None,
    ):
        key = self._key(user_id, lane, session_id, tenant_id=tenant_id, agent_id=agent_id)
        now = time.time()
        profile = self._profile(lane)
        with self._lock:
            if key not in self._buffers:
                self._buffers[key] = []
                self._first_msg[key] = now
            self._buffers[key].append(
                {
                    "content": content,
                    "title": title,
                    "ts": now,
                    "ingest_event_ids": [event_id] if event_id else [],
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "safety": content_safety or {},
                    "lane": lane,
                    "session_id": session_id or "",
                }
            )
            self._timestamps[key] = now
            buf = self._buffers[key]
            if (
                len(buf) >= profile["max_parts"]
                or sum(len(m["content"]) for m in buf) >= profile["max_chars"]
                or now - self._first_msg[key] >= profile["window"]
            ):
                return self._flush(key)
            return {"buffered": True, "count": len(buf)}

    def add_async(
        self,
        user_id,
        lane,
        content,
        title="",
        session_id="",
        *,
        event_id=None,
        event_factory=None,
        content_safety=None,
        tenant_id=None,
        agent_id=None,
    ):
        jid = self.job_id(
            user_id, lane, content, session_id, tenant_id=tenant_id, agent_id=agent_id
        )
        now = time.time()
        with self._lock:
            prev = self._seen.get(jid)
            if prev is not None and now - prev < self.IDEMPOTENT_TTL:
                original = self._seen_event_ids.get(jid)
                result = {"status": "queued", "job_id": jid, "duplicate": True}
                if original:
                    result["ingest_event_id"] = original
                return result
            self._pending_jobs.add(jid)
            try:
                if event_id is None and event_factory is not None:
                    event_id = event_factory()
                detail = self.add(
                    user_id,
                    lane,
                    content,
                    title,
                    session_id,
                    event_id=event_id,
                    content_safety=content_safety,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                )
                self._seen[jid] = now
                if event_id:
                    self._seen_event_ids[jid] = event_id
            finally:
                self._pending_jobs.discard(jid)
        result = {"status": "queued" if detail.get("buffered") else "flushed", "job_id": jid}
        if event_id:
            result["ingest_event_id"] = event_id
        if detail.get("flushed"):
            result["detail"] = detail
        return result

    def forget_fingerprint(self, job_id):
        with self._lock:
            self._seen.pop(job_id, None)
            self._seen_event_ids.pop(job_id, None)

    def requeue(self, key, items):
        now = time.time()
        with self._lock:
            self._buffers[key] = items + self._buffers.get(key, [])
            self._first_msg.setdefault(key, now)
            self._timestamps[key] = now

    def check_idle(self):
        flushed = []
        now = time.time()
        with self._lock:
            stale = [key for key, ts in self._seen.items() if now - ts >= self.IDEMPOTENT_TTL]
            for key in stale:
                self._seen.pop(key, None)
                self._seen_event_ids.pop(key, None)
            for key, items in list(self._buffers.items()):
                lane = items[0].get("lane") if items else None
                if lane is None:
                    parts = key.rsplit(":", 2)
                    lane = parts[-2] if len(parts) == 3 else parts[-1]
                if now - self._timestamps[key] >= self._profile(lane)["idle_timeout"]:
                    result = self._flush(key)
                    if result.get("flushed"):
                        flushed.append(result)
        return flushed

    def _flush(self, key):
        messages = self._buffers.pop(key, [])
        self._timestamps.pop(key, None)
        self._first_msg.pop(key, None)
        if not messages:
            return {"flushed": False}
        self._flush_count += 1
        self._last_flush_ts = time.time()
        first = messages[0]
        safety = {"secret_hits": []}
        for item in messages:
            for hit in (item.get("safety") or {}).get("secret_hits", []):
                if hit not in safety["secret_hits"]:
                    safety["secret_hits"].append(hit)
        parts = key.rsplit(":", 2)
        legacy_user, legacy_lane, legacy_session = (
            parts if len(parts) == 3 else ("default", parts[-1], "")
        )
        return {
            "flushed": True,
            "key": key,
            "count": len(messages),
            "lane": first.get("lane", legacy_lane),
            "session_id": first.get("session_id", legacy_session),
            "user_id": first.get("user_id", legacy_user),
            "tenant_id": first.get("tenant_id"),
            "agent_id": first.get("agent_id"),
            "combined_content": "\n".join(m["content"] for m in messages),
            "items": messages,
            "ingest_event_ids": list(
                dict.fromkeys(e for m in messages for e in m.get("ingest_event_ids", []))
            ),
            "safety": safety,
        }

    def water_level(self):
        with self._lock:
            return {
                "active_keys": len(self._buffers),
                "total_messages": sum(len(v) for v in self._buffers.values()),
                "seen_fingerprints": len(self._seen),
                "flush_count": self._flush_count,
                "last_flush_ts": self._last_flush_ts,
            }


_buffer = CoalesceBuffer()


def get_coalesce_buffer():
    return _buffer
