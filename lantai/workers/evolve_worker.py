from sqlmodel import select
from lantai.core.logger import logger
from lantai.models.tables import MemoryCandidate, MemoryProposal
from lantai.models.enums import ProposalStatus
from lantai.storage import db
from lantai.gate.decision import decide
from lantai.services.candidate_service import enqueue_rejected
from lantai.evolution.proposer import propose_from_candidate
from lantai.evolution.promoter import apply_proposal
from lantai.core.settings import settings
from lantai.core import scheduler as scheduler_mod


def run_evolve_once():
    with db.get_session() as s:
        cands = s.exec(select(MemoryCandidate)
                       .where(MemoryCandidate.status.in_(["new", "fastpath"]))).all()

    for cand in cands:
        result = decide(cand.id)
        if result["decision"] == "reject":
            # 校验失败（如低置信度）不再静默丢弃：进待审队列交用户裁决（Ticket 02）
            enqueue_rejected(cand.id)
            continue

        # archive_conflict（硬矛盾）不再丢弃新信息：走提案路径，
        # 由 proposer 生成 deprecate/update 纠正现有记忆。
        # 落地实战教训：旧记忆是错误提取（1116GB），新信息正确（16GB）——
        # 系统必须能"以新纠旧"，而不是把正确的纠正当矛盾丢掉。
        prop = propose_from_candidate(cand.id, result)

        # 自动应用规则：置信度足够高且无强冲突 → 自动 apply
        if prop.confidence >= 0.7 and not prop.conflict_ids:
            apply_proposal(prop.id)
        else:
            logger.info("proposal %s pending human review", prop.id)
    scheduler_mod.record_run("evolve")


def run_pending_proposals():
    with db.get_session() as s:
        props = s.exec(select(MemoryProposal)
                       .where(MemoryProposal.status == ProposalStatus.APPROVED)).all()
    for p in props:
        apply_proposal(p.id)
