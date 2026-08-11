"""演化与提案 service 层"""
from sqlmodel import select

from lantai.models.tables import MemoryProposal, MemoryCheckpoint
from lantai.models.schemas import ProposalDecisionReq, FeedbackReq
from lantai.models.enums import ProposalStatus
from lantai.storage import db
from lantai.evolution.promoter import apply_proposal, rollback
from lantai.evolution.reflector import record_feedback
from lantai.workers.evolve_worker import run_evolve_once


def list_proposals(status: str = "pending", limit: int = 50) -> dict:
    """列出提案。"""
    with db.get_session() as s:
        rows = s.exec(select(MemoryProposal)
                      .where(MemoryProposal.status == status)
                      .order_by(MemoryProposal.created_at.desc())
                      .limit(limit)).all()
        return {"proposals": [r.model_dump(mode="json") for r in rows]}


def decide_proposal(proposal_id: str, req: ProposalDecisionReq) -> dict:
    """批准或拒绝提案。"""
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        if not prop:
            raise ValueError("proposal not found")
        prop.decision_reason = req.reason or ""
        if req.approve:
            prop.status = ProposalStatus.APPROVED
            prop.decided_by = "user"
            s.add(prop); s.commit()
            return apply_proposal(proposal_id)
        else:
            prop.status = ProposalStatus.REJECTED
            prop.decided_by = "user"
            s.add(prop); s.commit()
            return {"ok": True}


def do_rollback(memory_id: str) -> dict:
    """回滚记忆。"""
    return rollback(memory_id)


def record_feedback_entry(req: FeedbackReq) -> dict:
    """记录反馈。"""
    return record_feedback(req.memory_id, req.query, req.helped,
                           req.user_accepted, req.hallucination_risk)


def run_evolve() -> dict:
    """运行演化 worker。"""
    run_evolve_once()
    return {"ok": True}


def list_checkpoints(memory_id: str, limit: int = 20) -> dict:
    """列出指定记忆的检查点。"""
    with db.get_session() as s:
        rows = s.exec(select(MemoryCheckpoint)
                      .where(MemoryCheckpoint.memory_id == memory_id)
                      .order_by(MemoryCheckpoint.version.desc())
                      .limit(limit)).all()
        return {"checkpoints": [r.model_dump(mode="json") for r in rows]}
