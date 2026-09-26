"""
检索事件日志（方向二弱标注源）——只追加，失败不影响主链路。

无标注评估集的现实替代：记录"哪条记忆被召回 + 当时生效参数 + 延迟"，
后续 dry-run/shadow 用这些事件做相对指标（zero_result / jaccard / 弱命中率）。
"""

import hashlib

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, RetrievalEvent
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash
from lantai.storage import db


def _norm_hash(query: str) -> str:
    digest = hashlib.sha256(" ".join(query.split()).lower().encode("utf-8"))
    return digest.hexdigest()


# 系统注入噪音的确定性前缀（Hermes 技能库维护 / 记忆保存 / Skill 安装模板）。
# 这类查询不是用户的真实记忆回忆，混入评估集会稀释 dry-run 指标。
_SYSTEM_NOISE_PREFIXES = (
    "review the conversation above",
    "consider saving to memory",
    "请帮我安装这个 agent skill",
)
# 长度阈值：实测存量数据 201-500 字符区间为 0 条——真实回忆查询几乎都 ≤200，
# >500 的基本是超长系统注入指令（技能库维护 prompt 达 5-7k 字符）。天然鸿沟，可安全判定。
_SYSTEM_NOISE_MAX_LEN = 500


def is_system_noise(query: str) -> bool:
    """判定一次检索查询是否为系统注入噪音（非用户真实记忆回忆）。

    纯函数、无副作用；宁 miss 不误标——只认确定性前缀 + 长度鸿沟，
    不依赖关键词匹配，避免把用户的真实长查询误判为噪音。
    """
    q = (query or "").strip()
    if not q:
        return False
    lower = q.lower()
    if any(lower.startswith(p) for p in _SYSTEM_NOISE_PREFIXES):
        return True
    return len(q) > _SYSTEM_NOISE_MAX_LEN


def log_retrieval(
    query: str,
    results: list[dict],
    *,
    latency_ms: int,
    gate: dict | None = None,
    trace_id: str | None = None,
    lanes: list[str] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> str | None:
    """
    在检索出口记录一次事件。失败仅记日志，绝不抛给主链路。
    返回事件 id（供后续回填 used_ids）；失败返回 None。

    session_id：来源链（v022 票据 05）——带 session 的检索才算「真实会话
    读」，写活性判据此计数；不透传则留空（如实 NULL，不猜测）。
    request_id：回执链（ADR-0049）——一次注入调用的整体标识，由注入侧
    （shell_hook context / 中间件）生成透传；不透传留空（pending 状态照记）。
    """
    try:
        event_id = new_id("rev")
        result_ids = [r["memory"]["id"] for r in results if isinstance(r, dict) and "memory" in r]
        result_scores = [r["score"] for r in results if isinstance(r, dict) and "score" in r]
        intent = (gate or {}).get("intent") if isinstance(gate, dict) else None
        from lantai.observability.recall_report import (
            _scenes_from_results,
            _tokens_from_results,
            estimate_tokens,
        )

        with db.get_session() as s:
            s.add(
                RetrievalEvent(
                    id=event_id,
                    trace_id=trace_id or "",
                    query_text=query,
                    query_norm_hash=_norm_hash(query),
                    lane=",".join(lanes) if lanes else "",
                    session_id=(session_id or "").strip() or None,
                    request_id=(request_id or "").strip() or None,
                    receipt_status="pending",
                    intent_bucket=intent if isinstance(intent, str) else None,
                    param_snapshot_hash=snapshot_hash(default_snapshot()),
                    result_ids=result_ids,
                    result_scores=result_scores,
                    used_ids=[],
                    latency_ms=int(latency_ms),
                    zero_result=not result_ids,
                    is_system_noise=is_system_noise(query),
                    scene_ids=_scenes_from_results(results),
                    estimated_tokens=(estimate_tokens(query) + _tokens_from_results(results)),
                )
            )
            s.commit()
        return event_id
    except Exception:
        logger.exception("retrieval event log failed (non-fatal)")
        return None


def backfill_used_ids(event_id: str, used_ids: list[str], request_id: str | None = None) -> None:
    """宿主回执：哪些被召回的记忆真正被用进回答（整体覆盖语义）。

    回执链一等化（ADR-0049）：回执成功 → receipt_status="acked" + receipt_at 落定。
    request_id 提供时与事件列核对——不一致如实记日志（不拒绝、不改状态；
    宁 miss 不脏写，回执归属以 event_id 为准）。
    """
    try:
        with db.get_session() as s:
            ev = s.get(RetrievalEvent, event_id)
            if ev:
                if request_id and ev.request_id and request_id != ev.request_id:
                    logger.warning(
                        "receipt request_id mismatch (kept by event_id): ev=%s ev_req=%s got=%s",
                        event_id,
                        ev.request_id,
                        request_id,
                    )
                ev.used_ids = list(used_ids)
                ev.receipt_status = "acked"
                ev.receipt_at = utcnow()
                s.add(ev)
                s.commit()
    except Exception:
        logger.exception("retrieval used_ids backfill failed (non-fatal)")


RECEIPT_MISS_TIMEOUT_SECONDS = 300


def mark_missed_receipts(timeout_seconds: int = RECEIPT_MISS_TIMEOUT_SECONDS, now=None) -> int:
    """回执超时判定（ADR-0049）：pending 且超龄 → missed，返回置位数。

    missed 是事实不是错误（宁 miss 不脏写）：宿主未回执的事件如实置位，
    使「回执缺失」可观测、可统计，而不是永远 pending。幂等：已置位不重算。
    """
    from datetime import timedelta

    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=timeout_seconds)
    try:
        with db.get_session() as s:
            rows = s.exec(
                select(RetrievalEvent).where(
                    RetrievalEvent.receipt_status == "pending",
                    RetrievalEvent.created_at < cutoff,
                )
            ).all()
            for ev in rows:
                ev.receipt_status = "missed"
                ev.receipt_at = moment
                s.add(ev)
            if rows:
                s.commit()
            return len(rows)
    except Exception:
        logger.exception("receipt miss marking failed (non-fatal)")
        return 0


def receipt_traceability_report() -> dict:
    """可追溯率统计出口（ADR-0049；票 06 宿主冒烟与自证复用）。

    口径（票据 04 交付 4）：acked 事件中，used_ids 非空且每个 used_id 均能
    回溯到现存 MemoryItem 行的比例（missed/pending 不入分子）。全部计数如实
    分列，不混计；无 acked 样本时 rate 如实返回 None（不编造）。
    """
    try:
        with db.get_session() as s:
            status_rows = s.exec(
                select(RetrievalEvent.receipt_status, RetrievalEvent.used_ids)
            ).all()
            acked_used: list[list[str]] = []
            pending = missed = acked_plain = 0
            for status, used_ids in status_rows:
                if status == "acked":
                    if used_ids:
                        acked_used.append(list(used_ids))
                    else:
                        acked_plain += 1
                elif status == "missed":
                    missed += 1
                else:
                    pending += 1
        traceable = 0
        if acked_used:
            with db.get_session() as s2:
                for used in acked_used:
                    existing = [uid for uid in used if s2.get(MemoryItem, uid) is not None]
                    if used and len(existing) == len(used):
                        traceable += 1
        acked_total = len(acked_used) + acked_plain
        return {
            "pending": pending,
            "acked": acked_total,
            "acked_with_used_ids": len(acked_used),
            "missed": missed,
            "traceable": traceable,
            # 口径：acked 事件中可完整回溯的比例（used_ids 空的 acked 不入分子）
            "traceability_rate": (round(traceable / len(acked_used), 4) if acked_used else None),
        }
    except Exception:
        logger.exception("receipt traceability report failed (non-fatal)")
        return {}
