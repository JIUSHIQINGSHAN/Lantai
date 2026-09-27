"""演化与提案 service 层"""

from sqlmodel import select

from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal, rollback
from lantai.evolution.reflector import record_feedback
from lantai.models.enums import ProposalStatus
from lantai.models.schemas import FeedbackReq, ProposalDecisionReq
from lantai.models.tables import MemoryCheckpoint, MemoryProposal
from lantai.storage import db
from lantai.workers.evolve_worker import run_evolve_once


def _proposal_viewer(principal) -> str:
    """读侧收敛到的 user_id（票 .scratch/readside-gaps/02）。

    口径同 `candidate_service._viewer_of`：None / 空 user_id → `"default"`。
    """
    return (
        (getattr(principal, "user_id", None) or "default") if principal is not None else "default"
    )


def _proposal_scope(principal):
    """归属过滤条件：非 admin 只见自己的提案；admin/system 全权（返回 None）。

    NULL 属主老行一律不可见（同 candidate_service 口径：真实库的提案行
    有属主，NULL 只可能是迁移前的历史行，放行就是把越权口子留着）。
    """
    if principal is not None and bool(getattr(principal, "is_admin", False)):
        return None
    return MemoryProposal.user_id == _proposal_viewer(principal)


def _ensure_can_decide(principal, proposal: MemoryProposal) -> None:
    """裁决提案的归属校验（票 .scratch/readside-gaps/02）。

    裁决 = 破坏性操作，复用 `acl.ensure_can_delete` 单一真源。此前 A 能
    对 B 的提案 decide——**approve 会直接 apply_proposal 写库**，比拒候选
    更重：A 能让 B 的待决内容凭空变成正式记忆。
    principal=None 仅限内部调用（CLI/worker/MCP），不校验。
    """
    if principal is None:
        return
    from lantai.core.acl import ensure_can_delete

    ensure_can_delete(
        principal,
        resource_user_id=proposal.user_id,
        resource_tenant_id=proposal.tenant_id,
    )


def list_proposals(status: str = "pending", limit: int = 50, *, principal=None) -> dict:
    """列出提案。

    归属收窄（票 .scratch/readside-gaps/02）：此前全表捞，`/proposals`
    把任何用户的待决提案（`reason` 常含记忆正文）吐给任何登录用户。
    """
    with db.get_session() as s:
        stmt = select(MemoryProposal).where(MemoryProposal.status == status)
        scope = _proposal_scope(principal)
        if scope is not None:
            stmt = stmt.where(scope)
        rows = s.exec(stmt.order_by(MemoryProposal.created_at.desc()).limit(limit)).all()
        return {"proposals": [r.model_dump(mode="json") for r in rows]}


def decide_proposal(proposal_id: str, req: ProposalDecisionReq, *, principal=None) -> dict:
    """批准或拒绝提案。

    归属校验（票 .scratch/readside-gaps/02）：approve 会 `apply_proposal`
    直接写库，A 能对 B 的提案点批准就等于能往库里塞内容。校验在 service
    层（worker/MCP 也走这条路）。
    """
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        if not prop:
            raise ValueError("proposal not found")
        if prop.status != ProposalStatus.PENDING:
            raise RuntimeError("proposal state changed; refresh and retry")
        if not req.approve and not (req.reason or "").strip():
            raise ValueError("reject reason is required")
        _ensure_can_decide(principal, prop)
        prop.decision_reason = req.reason or ""
        # decided_at（ADR-0053）：裁决即落时刻，不等 apply——approve 与 reject 同口径。
        # 巩固拒绝冷却期按此起算（ADR-0050 边界「冷却期起算点用 created_at 近似」的修法）。
        prop.decided_at = utcnow()
        if req.approve:
            prop.status = ProposalStatus.APPROVED
            prop.decided_by = "user"
            s.add(prop)
            s.commit()
            return apply_proposal(proposal_id)
        else:
            prop.status = ProposalStatus.REJECTED
            prop.decided_by = "user"
            s.add(prop)
            s.commit()
            return {"ok": True}


def do_rollback(memory_id: str) -> dict:
    """回滚记忆。"""
    return rollback(memory_id)


def record_feedback_entry(req: FeedbackReq) -> dict:
    """记录反馈。"""
    return record_feedback(
        req.memory_id, req.query, req.helped, req.user_accepted, req.hallucination_risk
    )


def run_evolve() -> dict:
    """运行演化 worker。"""
    run_evolve_once()
    return {"ok": True}


def list_checkpoints(memory_id: str, limit: int = 20) -> dict:
    """列出指定记忆的检查点。"""
    with db.get_session() as s:
        rows = s.exec(
            select(MemoryCheckpoint)
            .where(MemoryCheckpoint.memory_id == memory_id)
            .order_by(MemoryCheckpoint.version.desc())
            .limit(limit)
        ).all()
        return {"checkpoints": [r.model_dump(mode="json") for r in rows]}
