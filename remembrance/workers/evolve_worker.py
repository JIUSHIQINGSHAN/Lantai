from sqlmodel import select
from remembrance.core.logger import logger
from remembrance.models.tables import MemoryCandidate, MemoryProposal
from remembrance.models.enums import ProposalStatus
from remembrance.storage import db
from remembrance.gate.decision import decide
from remembrance.evolution.proposer import propose_from_candidate
from remembrance.evolution.promoter import apply_proposal
from remembrance.core.settings import settings


def run_evolve_once():
    with db.get_session() as s:
        cands = s.exec(select(MemoryCandidate)
                       .where(MemoryCandidate.status == "new")).all()

    for cand in cands:
        result = decide(cand.id)
        if result["decision"] in ("reject", "archive_conflict"):
            with db.get_session() as s:
                c = s.get(MemoryCandidate, cand.id)
                c.status = "rejected"; s.add(c); s.commit()
            continue

        prop = propose_from_candidate(cand.id, result)

        # 自动应用规则：置信度足够高且无强冲突 → 自动 apply
        if prop.confidence >= 0.7 and not prop.conflict_ids:
            apply_proposal(prop.id)
        else:
            logger.info("proposal %s pending human review", prop.id)


def run_pending_proposals():
    with db.get_session() as s:
        props = s.exec(select(MemoryProposal)
                       .where(MemoryProposal.status == ProposalStatus.APPROVED)).all()
    for p in props:
        apply_proposal(p.id)
