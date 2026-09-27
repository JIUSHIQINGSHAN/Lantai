from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.evolution.promoter import VALID_PROPOSAL_TYPES
from lantai.llm.client import chat_json
from lantai.llm.prompts import PROPOSAL_SYS
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal
from lantai.services.prompt_service import get_prompt
from lantai.storage import db

# 提案类型白名单：单一来源在 promoter.VALID_PROPOSAL_TYPES（apply 侧是最终
# 执行者，类型合法性由它定义）。此处复用而非另立一份，避免两处漂移。


def _resolve_target_id(session, target_key: str) -> str:
    """把 LLM 返回的 target_key 解析为**唯一** active MemoryItem 的 id。

    空 / 解析不到 / 多义（`MemoryItem.key` 无 unique 约束）一律返回 ""，
    由调用方整条丢弃提案——不在这一层猜（猜即脏写）。
    """
    if not target_key:
        return ""
    matches = session.exec(
        select(MemoryItem).where(MemoryItem.key == target_key, MemoryItem.status == "active")
    ).all()
    return matches[0].id if len(matches) == 1 else ""


def propose_from_candidate(candidate_id: str, gate_result: dict) -> MemoryProposal | None:
    """候选 → 提案（LLM 产出 + 目标寻址 + 落库）。

    返回 `None` 表示**整条丢弃**：update/merge/deprecate 提案的 `target_key`
    解析不到唯一 active 记忆（空/无匹配/多义）。调用方须判空——
    宁 miss 不脏写，不降级为 add（那会新建平行记忆）。
    详见 `.scratch/proposal-target-gap/issues/01-*.md`。
    """
    with db.get_session() as s:
        cand = s.get(MemoryCandidate, candidate_id)
        related = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
        existing_snippets = "\n".join(
            f"- ({m.memory_type}) {m.key}: {m.content}" for m in related[:20]
        )
        user = (
            f"CANDIDATE SUMMARY:\n{cand.summary}\n"
            f"CLAIMS:\n{cand.claims}\nACTIONS:\n{cand.actions}\n\n"
            f"EXISTING:\n{existing_snippets or '(none)'}\n\n"
            f"GATE:\n{gate_result}"
        )
        try:
            data = chat_json(get_prompt("PROPOSAL_SYS", PROPOSAL_SYS), user)
        except Exception:
            data = {
                "proposal_type": "add",
                "target_key": "",
                "new_content": cand.summary,
                "memory_type": "semantic",
                "reason": "fallback add",
                "confidence": 0.4,
            }

        # Skill 资产化：提取的 actions（步骤）沉淀为 structure，随提案落库
        # （proposer → promoter 全链路保留，否则步骤在提案应用后丢失）
        structure = {}
        if cand.actions:
            structure = {
                "name": (data.get("target_key") or cand.summary[:40]),
                "description": (cand.summary or "")[:200],
                "steps": cand.actions,
            }

        ptype = data.get("proposal_type", "add")
        # 类型白名单（票据 `.scratch/proposal-type-whitelist/`）：LLM 自由文本
        # 不构成约束——模型输出 split/link/大小写变体时，apply 侧兜底分支会把
        # 它当 add 执行（新建平行记忆 + 多出 supports 边，而本意一件没做）。
        # 对齐 reflector.propose_from_reflection 的 `_VALID_TYPES` + continue 范式。
        if ptype not in VALID_PROPOSAL_TYPES:
            logger.warning(
                "候选 %s 的提案丢弃：proposal_type %r 不在白名单 %s"
                "（宁 miss 不脏写，不降级为 add）",
                candidate_id,
                ptype,
                sorted(VALID_PROPOSAL_TYPES),
            )
            cand.status = "gated"
            s.add(cand)
            s.commit()
            return None
        # 目标寻址（票据 `.scratch/proposal-target-gap/issues/01-*.md`）：LLM 按
        # PROPOSAL_SYS 返回的是 target_key，而 MemoryProposal 寻址读的是
        # target_memory_id——不在此处解析则非 add 提案的 target 恒为 NULL，
        # 下游 promoter 只能拿 key 字符串猜（实证真实库 72 条非 add 提案中 51 条
        # 无 target，43% 因此新建平行记忆）。
        target_id = ""
        if ptype in ("update", "merge", "deprecate"):
            target_id = _resolve_target_id(s, data.get("target_key") or "")
            if not target_id:
                # 解析不到唯一 active 目标 → 整条不生成（对齐 reflector.
                # propose_from_reflection 的 continue 范式，宁 miss 不脏写）。
                # **不降级为 add**：那正是平行记忆脏写的成因。
                logger.warning(
                    "候选 %s 的 %s 提案丢弃：target_key %r 解析不到唯一 active 记忆"
                    "（宁 miss 不脏写，不降级为 add）",
                    candidate_id,
                    ptype,
                    data.get("target_key") or "",
                )
                cand.status = "gated"
                s.add(cand)
                s.commit()
                return None
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type=ptype,
            target_memory_id=target_id or None,
            candidate_id=candidate_id,
            evidence_ids=[cand.document_id],
            # 来源链继承（v022 票据 01）：出身随候选显式流动，不靠隐式通道
            user_id=getattr(cand, "user_id", None),
            session_id=getattr(cand, "session_id", None),
            reason=data.get("reason", ""),
            proposed_patch={
                "memory_type": data.get("memory_type", "semantic"),
                "key": data.get("target_key") or cand.summary[:60],
                "content": data.get("new_content", cand.summary),
                "lane": cand.lane,
                "structure": structure,
                # 更漏（ADR-0048/票 08）：候选 provenance 的显式事件时间随链透传
                # （低置信不猜——提取侧已保证只在确定性格式命中时写入）
                **(
                    {
                        "event_time": cand.provenance["event_time"],
                        "event_time_precision": cand.provenance["event_time_precision"],
                    }
                    if isinstance(cand.provenance, dict) and cand.provenance.get("event_time")
                    else {}
                ),
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
