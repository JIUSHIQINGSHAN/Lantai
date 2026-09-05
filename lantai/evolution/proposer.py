from sqlmodel import select

from lantai.core.ids import new_id
from lantai.llm.client import chat_json
from lantai.llm.prompts import PROPOSAL_SYS
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal
from lantai.storage import db


def propose_from_candidate(candidate_id: str, gate_result: dict) -> MemoryProposal:
    with db.get_session() as s:
        cand = s.get(MemoryCandidate, candidate_id)
        related = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
        existing_snippets = "\n".join(f"- ({m.memory_type}) {m.key}: {m.content}"
                                      for m in related[:20])
        user = (f"CANDIDATE SUMMARY:\n{cand.summary}\n"
                f"CLAIMS:\n{cand.claims}\nACTIONS:\n{cand.actions}\n\n"
                f"EXISTING:\n{existing_snippets or '(none)'}\n\n"
                f"GATE:\n{gate_result}")
        try:
            data = chat_json(PROPOSAL_SYS, user)
        except Exception:
            data = {"proposal_type": "add", "target_key": "",
                    "new_content": cand.summary, "memory_type": "semantic",
                    "reason": "fallback add", "confidence": 0.4}

        # Skill 资产化：提取的 actions（步骤）沉淀为 structure，随提案落库
        # （proposer → promoter 全链路保留，否则步骤在提案应用后丢失）
        structure = {}
        if cand.actions:
            structure = {
                "name": (data.get("target_key") or cand.summary[:40]),
                "description": (cand.summary or "")[:200],
                "steps": cand.actions,
            }
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type=data.get("proposal_type", "add"),
            candidate_id=candidate_id,
            evidence_ids=[cand.document_id],
            reason=data.get("reason", ""),
            proposed_patch={
                "memory_type": data.get("memory_type", "semantic"),
                "key": data.get("target_key") or cand.summary[:60],
                "content": data.get("new_content", cand.summary),
                "lane": cand.lane,
                "structure": structure,
            },
            confidence=float(data.get("confidence", 0.5)),
            conflict_ids=[c["memory_id"] for c in gate_result.get("conflicts", [])],
            status=ProposalStatus.PENDING,
            provenance=cand.provenance or {},
        )
        s.add(prop)
        cand.status = "gated"
        s.add(cand)
        s.commit()
        s.refresh(prop)
        return prop
