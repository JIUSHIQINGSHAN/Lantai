"""候选可见队列 service 层（Ticket 02）

语义：被闸门拒绝的候选不再静默丢弃——进待审队列（pending_review），
由用户 list/review 裁决；超龄（CANDIDATE_TTL_DAYS）自动归档为 rejected。
"""
from datetime import timedelta, timezone

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


def review_candidate(candidate_id: str, approve: bool, reason: str = "") -> dict:
    """人工审核候选。

    approve：用户已裁决，直接进提案链并立即应用（不再重复走 gate）；
             proposer 内部把候选置为 gated，闭环到 proposal→memory。
    reject：标记归档（rejected），清空 review_due_at。
    """
    with db.get_session() as s:
        c = s.get(MemoryCandidate, candidate_id)
        if not c:
            raise ValueError("candidate not found")
        if c.status != "pending_review":
            raise ValueError(f"candidate not pending (status={c.status})")

    if not approve:
        with db.get_session() as s:
            c = s.get(MemoryCandidate, candidate_id)
            c.status = "rejected"
            c.review_due_at = None
            s.add(c)
            s.commit()
            return {"ok": True, "candidate_status": "rejected"}

    from lantai.evolution.proposer import propose_from_candidate
    from lantai.evolution.promoter import apply_proposal
    gate_result = {
        "decision": "working_only",
        "novelty": 1.0,
        "conflicts": [],
        "reason": reason or "approved by user review",
    }
    prop = propose_from_candidate(candidate_id, gate_result)
    applied = apply_proposal(prop.id)
    return {"ok": True, "proposal_id": prop.id,
            "applied": applied.get("ok", False),
            "candidate_status": "gated"}


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
                c.review_due_at = None
                s.add(c)
                archived += 1
        s.commit()
        return {"ok": True, "archived": archived}
