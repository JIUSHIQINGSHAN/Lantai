"""冷启动导入服务（借鉴 TencentDB Agent Memory L0 会话记录 + v2.0.1 时间戳修正）。

历史会话 JSONL（每行一条消息，与腾讯 L0 同款：{role, content, timestamp[, session]}）
批量喂既有对话摄取链，保留原始时间戳——RawDocument.fetched_at / MemoryCandidate
created_at = 消息时间，provenance.prompt = dialogue-session-import，随演化链
继承到 MemoryItem（时间线不压平到导入时刻）。

- 只导入 user 消息（assistant 行跳过计入 stats）
- 单行失败不拖停整批（解析失败计数，宁 miss 不脏写）
- dry_run 只解析不写库（预览统计）
"""
import json
import time
from datetime import datetime, timezone

from lantai.core.settings import settings


def normalize_timestamp(value) -> datetime:
    """时间戳归一化 → naive UTC datetime（纯函数）。

    接受 epoch 毫秒（≥1e11）/ epoch 秒（≥1e9）/ ISO-8601 字符串（含 Z/±时区）；
    非法输入抛 ValueError。
    """
    if isinstance(value, bool) or value is None:
        raise ValueError("timestamp must be numeric or ISO string")
    if isinstance(value, (int, float)):
        if value >= 1e11:
            return datetime.fromtimestamp(value / 1000.0,
                                          tz=timezone.utc).replace(tzinfo=None)
        if value >= 1e9:
            return datetime.fromtimestamp(value,
                                          tz=timezone.utc).replace(tzinfo=None)
        raise ValueError(f"timestamp out of range: {value}")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("empty timestamp")
        try:
            return normalize_timestamp(float(s))
        except ValueError:
            pass
        iso = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            raise ValueError(f"invalid ISO timestamp: {value}") from None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    raise ValueError(f"unsupported timestamp type: {type(value).__name__}")


def parse_session_line(raw: str) -> dict | None:
    """解析单行 JSONL 会话消息（纯函数）。

    合法行 → {"role", "content", "ts"(naive UTC), "session"}；
    非法 JSON / 缺字段 / 空内容 / 非法时间戳 → None（统计为解析失败，不抛）。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    role = data.get("role", "")
    content = data.get("content", "")
    if role not in ("user", "assistant"):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    ts_raw = data.get("timestamp")
    if ts_raw is None:
        return None
    try:
        ts = normalize_timestamp(ts_raw)
    except ValueError:
        return None
    session = data.get("session") or data.get("session_id") or data.get("sessionKey") or ""
    return {"role": role, "content": content.strip(), "ts": ts,
            "session": str(session) if session else ""}


def _parse_all(path: str, max_lines: int | None) -> tuple[list, dict]:
    """逐行解析 JSONL：返回 (消息列表, 统计)。解析失败不抛。"""
    limit = max_lines if max_lines is not None else settings.IMPORT_MAX_LINES
    messages = []
    stats = {"lines": 0, "parsed": 0, "errors": 0, "skipped_assistant": 0,
             "sessions": 0}
    session_ids = set()
    with open(path, encoding="utf-8") as f:
        for raw in f:
            stats["lines"] += 1
            if limit and stats["lines"] > limit:
                break
            msg = parse_session_line(raw)
            if msg is None:
                stats["errors"] += 1
                continue
            stats["parsed"] += 1
            if msg["session"]:
                session_ids.add(msg["session"])
            if msg["role"] != "user":
                stats["skipped_assistant"] += 1
                continue
            messages.append(msg)
    stats["sessions"] = len(session_ids)
    return messages, stats


def import_session_jsonl(path: str, *, dry_run: bool = False,
                         user_id: str = "default",
                         max_lines: int | None = None) -> dict:
    """批量导入历史会话 JSONL（冷启动，保留原始时间戳）。

    - dry_run：只解析不写库（预览统计，零副作用）
    - 非 dry_run：user 消息逐条喂 ingest_dialogue（fastpath 直通 / 提取建候选 /
      闲聊入待审队列），created_at = 消息原始时间戳
    - 单行摄取失败只计数不拖停整批（宁 miss 不脏写）
    """
    started = time.monotonic()
    messages, stats = _parse_all(path, max_lines)
    statuses: dict = {}
    ingest_errors = 0
    if not dry_run and messages:
        from lantai.ingestion.dialogue import ingest_dialogue
        for msg in messages:
            try:
                res = ingest_dialogue(msg["content"], user_id=user_id,
                                      source="session_import",
                                      created_at=msg["ts"])
                statuses[res["status"]] = statuses.get(res["status"], 0) + 1
            except Exception:
                ingest_errors += 1
    stats["ingest_errors"] = ingest_errors
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "path": str(path),
        "lines": stats["lines"],
        "parsed": stats["parsed"],
        "errors": stats["errors"],
        "skipped_assistant": stats["skipped_assistant"],
        "sessions": stats["sessions"],
        "imported": 0 if dry_run else len(messages) - ingest_errors,
        "would_import": len(messages),  # 预览口径一致：本批 user 消息目标条数（真实模式不随错误缩水）
        "statuses": statuses,
        "took_ms": int((time.monotonic() - started) * 1000),
    }