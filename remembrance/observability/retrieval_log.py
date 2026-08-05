"""
检索事件日志（方向二弱标注源）——只追加，失败不影响主链路。

无标注评估集的现实替代：记录"哪条记忆被召回 + 当时生效参数 + 延迟"，
后续 dry-run/shadow 用这些事件做相对指标（zero_result / jaccard / 弱命中率）。
"""
import hashlib
import time

from remembrance.core.ids import new_id
from remembrance.core.logger import logger
from remembrance.core.time import utcnow
from remembrance.models.tables import RetrievalEvent
from remembrance.parameters.registry import default_snapshot
from remembrance.parameters.validation import snapshot_hash
from remembrance.storage import db


def _norm_hash(query: str) -> str:
    digest = hashlib.sha256(" ".join(query.split()).lower().encode("utf-8"))
    return digest.hexdigest()


def log_retrieval(query: str, results: list[dict], *, latency_ms: int,
                  gate: dict | None = None, trace_id: str | None = None,
                  lanes: list[str] | None = None) -> str | None:
    """
    在检索出口记录一次事件。失败仅记日志，绝不抛给主链路。
    返回事件 id（供后续回填 used_ids）；失败返回 None。
    """
    try:
        event_id = new_id("rev")
        result_ids = [r["memory"]["id"] for r in results
                      if isinstance(r, dict) and "memory" in r]
        result_scores = [r["score"] for r in results
                         if isinstance(r, dict) and "score" in r]
        intent = (gate or {}).get("intent") if isinstance(gate, dict) else None
        with db.get_session() as s:
            s.add(RetrievalEvent(
                id=event_id,
                trace_id=trace_id or "",
                query_text=query,
                query_norm_hash=_norm_hash(query),
                lane=",".join(lanes) if lanes else "",
                intent_bucket=intent if isinstance(intent, str) else None,
                param_snapshot_hash=snapshot_hash(default_snapshot()),
                result_ids=result_ids,
                result_scores=result_scores,
                used_ids=[],
                latency_ms=int(latency_ms),
                zero_result=not result_ids,
            ))
            s.commit()
        return event_id
    except Exception:
        logger.exception("retrieval event log failed (non-fatal)")
        return None


def backfill_used_ids(event_id: str, used_ids: list[str]) -> None:
    """生成侧回填：哪些被召回的记忆真正被用进回答（弱标注）。"""
    try:
        with db.get_session() as s:
            ev = s.get(RetrievalEvent, event_id)
            if ev:
                ev.used_ids = list(used_ids)
                s.add(ev)
                s.commit()
    except Exception:
        logger.exception("retrieval used_ids backfill failed (non-fatal)")
