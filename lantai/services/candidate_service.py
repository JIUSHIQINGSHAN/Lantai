"""候选可见队列 service 层（Ticket 02）

语义：被闸门拒绝的候选不再静默丢弃——进待审队列（pending_review），
由用户 list/review 裁决；超龄（CANDIDATE_TTL_DAYS）自动归档为 rejected。
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate
from lantai.storage import db


def enqueue_rejected(candidate_id: str) -> None:
    """reject 候选入待审队列：pending_review + review_due_at = now + TTL。

    幂等：已 pending_review / 已归档 / 不存在时不重复操作。
    """
    with db.get_session() as s:
        c = s.get(MemoryCandidate, candidate_id)
        if not c or c.status in ("pending_review", "rejected", "gated"):
            return
        c.status = "pending_review"
        c.review_due_at = utcnow() + timedelta(days=settings.CANDIDATE_TTL_DAYS)
        s.add(c)
        s.commit()


def list_pending_candidates(limit: int = 50) -> dict:
    """待审候选列表：按 review_due_at 升序（最紧迫在前）。"""
    with db.get_session() as s:
        rows = s.exec(select(MemoryCandidate)
                      .where(MemoryCandidate.status == "pending_review")
                      .order_by(MemoryCandidate.review_due_at.asc())
                      .limit(limit)).all()
        return {"candidates": [r.model_dump(mode="json") for r in rows]}


class CandidateStateConflict(ValueError):
    """候选已被其他入口修改，调用方必须刷新后重试。"""


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _same_time(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs((_aware(left) - _aware(right)).total_seconds()) < 0.001


def _clear_defer_state(candidate: MemoryCandidate) -> None:
    candidate.review_due_at = None
    candidate.deferred_at = None
    candidate.previous_review_due_at = None
    candidate.defer_reason = ""


def review_candidate(candidate_id: str, approve: bool, reason: str = "") -> dict:
    """人工审核候选。

    approve：用户已裁决，只创建 pending 提案（不再重复走 gate）；
             最终写入必须再次批准提案。
    reject：标记归档（rejected），清空 review_due_at。
    """
    with db.get_session() as s:
        c = s.get(MemoryCandidate, candidate_id)
        if not c:
            raise ValueError("candidate not found")
        if c.status != "pending_review":
            raise ValueError(f"candidate not pending (status={c.status})")

    if not approve:
        if not (reason or "").strip():
            raise ValueError("reject reason is required")
        with db.get_session() as s:
            c = s.get(MemoryCandidate, candidate_id)
            if not c or c.status != "pending_review":
                raise CandidateStateConflict("candidate state changed; refresh and retry")
            c.status = "rejected"
            _clear_defer_state(c)
            s.add(c)
            s.commit()
            return {"ok": True, "candidate_status": "rejected", "reason": reason.strip()}

    from lantai.evolution.proposer import propose_from_candidate
    gate_result = {
        "decision": "working_only",
        "novelty": 1.0,
        "conflicts": [],
        "reason": reason or "approved by user review",
    }
    prop = propose_from_candidate(candidate_id, gate_result)
    return {"ok": True, "proposal_id": prop.id, "proposal_status": "pending",
            "applied": False, "candidate_status": "gated"}


def defer_candidate(candidate_id: str, days: int, reason: str = "",
                    expected_review_due_at: datetime | None = None) -> dict:
    """延期 3/7 天；最长不超过首次创建后 30 天，保留一次撤销所需旧值。"""
    if days not in (3, 7):
        raise ValueError("days must be 3 or 7")
    with db.get_session() as s:
        c = s.get(MemoryCandidate, candidate_id)
        if not c:
            raise ValueError("candidate not found")
        if c.status != "pending_review":
            raise CandidateStateConflict("candidate state changed; refresh and retry")
        if expected_review_due_at is not None and not _same_time(
                c.review_due_at, expected_review_due_at):
            raise CandidateStateConflict("candidate due date changed; refresh and retry")
        now = utcnow()
        next_due = now + timedelta(days=days)
        max_due = _aware(c.created_at) + timedelta(days=30)
        if next_due > max_due:
            raise ValueError("candidate cannot be deferred beyond 30 days from creation")
        c.previous_review_due_at = c.review_due_at
        c.review_due_at = next_due
        c.deferred_at = now
        c.defer_count = int(c.defer_count or 0) + 1
        c.defer_reason = (reason or "").strip()[:500]
        s.add(c)
        s.commit()
        return {
            "ok": True, "candidate_id": candidate_id,
            "review_due_at": next_due.isoformat(),
            "previous_review_due_at": (
                _aware(c.previous_review_due_at).isoformat()
                if c.previous_review_due_at else None),
            "defer_count": c.defer_count,
        }


def undo_candidate_defer(candidate_id: str,
                         expected_review_due_at: datetime | None = None) -> dict:
    """撤销最近一次延期；状态或截止时间变化时拒绝覆盖。"""
    with db.get_session() as s:
        c = s.get(MemoryCandidate, candidate_id)
        if not c:
            raise ValueError("candidate not found")
        if c.status != "pending_review":
            raise CandidateStateConflict("candidate state changed; refresh and retry")
        if expected_review_due_at is not None and not _same_time(
                c.review_due_at, expected_review_due_at):
            raise CandidateStateConflict("candidate due date changed; refresh and retry")
        if c.previous_review_due_at is None or c.deferred_at is None:
            raise ValueError("candidate has no defer action to undo")
        restored = c.previous_review_due_at
        c.review_due_at = restored
        c.previous_review_due_at = None
        c.deferred_at = None
        c.defer_count = max(0, int(c.defer_count or 0) - 1)
        c.defer_reason = ""
        s.add(c)
        s.commit()
        return {"ok": True, "candidate_id": candidate_id,
                "review_due_at": _aware(restored).isoformat()}


def run_candidate_ttl_once() -> dict:
    """TTL 任务：超龄（review_due_at < now）的 pending_review 自动归档。"""
    archived = 0
    with db.get_session() as s:
        rows = s.exec(select(MemoryCandidate)
                      .where(MemoryCandidate.status == "pending_review")).all()
        now = utcnow()
        for c in rows:
            due = c.review_due_at
            if due is None:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due <= now:
                c.status = "rejected"
                _clear_defer_state(c)
                s.add(c)
                archived += 1
        s.commit()
        return {"ok": True, "archived": archived}
