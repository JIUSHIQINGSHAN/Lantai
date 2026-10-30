"""披沙（ADR-0077）：候选记忆递归提纯去噪（Pisha）核心服务。

基于认知层 PishaEngine 统一封装：
1. refine_memory_text: LLM 与确定性启发式多轨提纯（纯函数，异常时宁 miss 不脏写降级）；
2. refine_candidate_record: 针对单条候选记录进行精炼并更新 DB；
3. batch_refine_candidates: 针对模糊区间候选批量披沙提纯。
"""

from typing import Any

from sqlmodel import Session, select

from lantai.cognition.pisha import PishaEngine
from lantai.core.logger import logger
from lantai.models.tables import MemoryCandidate
from lantai.storage import db


def refine_memory_text(text: str, context: str = "", metadata: dict | None = None) -> dict:
    """对单段记忆文本执行指代消解与结构化提纯（基于 PishaEngine，纯函数，异常时宁 miss 不脏写降级）。"""
    engine = PishaEngine()
    entity_hints = metadata.get("entity_hints") if metadata else None
    res = engine.refine_text(text=text, context=context, entity_hints=entity_hints)
    return {
        "refined_text": res.refined_text,
        "confidence": res.refined_confidence,
        "is_valid": res.is_valid,
        "llm_failed": res.llm_failed,
        "lane": res.lane,
        "tags": res.denoise_tags,
        "reason": res.reason,
        "pronoun_resolutions": res.pronoun_resolutions,
        "fenced_content": res.fenced_content,
    }


def refine_candidate_record(
    candidate_id: str, session: Session | None = None, *, principal=None
) -> dict:
    """对指定 ID 的候选记录执行精炼并落库。

    归属校验（票 .scratch/readside-gaps/02）：refine 会改写 B 的 summary、
    claims、lane，甚至在 `is_valid=false` 时把 status 改成 rejected——**A 对
    B 的候选点一次"提纯"就可能把它永久驳回**。同 review/defer 口径，复用
    `acl.ensure_can_delete`；`principal=None` 仅内部调用（CLI/worker/MCP），
    不校验——硬造校验会把 `run_triage_auto_pilot` 这类无人值守流程修废。
    """
    cand_id = (candidate_id or "").strip()
    if not cand_id:
        raise ValueError("candidate_id 不能为空")

    def _run(s: Session) -> dict:
        cand = s.get(MemoryCandidate, cand_id)
        if not cand:
            raise ValueError(f"候选记录未找到: {cand_id}")

        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=cand.user_id,
                resource_tenant_id=cand.tenant_id,
                lane=cand.lane,
            )

        engine = PishaEngine(session=s, principal=principal)
        raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
        context_str = str(cand.provenance) if cand.provenance else ""
        initial_conf = float(cand.extractor_confidence or 0.4)

        res = engine.refine_text(raw_text, context=context_str, initial_confidence=initial_conf)
        if res.llm_failed and not res.denoise_tags:
            logger.warning("披沙:候选【%s】LLM 不可用且无规则修剪,保持原样待重试", cand.id)
            return cand.model_dump(mode="json")

        if not res.is_valid or res.refined_confidence <= 0.0:
            cand.status = "rejected"
            cand.extractor_confidence = 0.0
            cand.provenance = {
                **(cand.provenance or {}),
                "pisha_rejected": True,
                "pisha_reason": res.reason,
            }
        else:
            cand.summary = res.refined_text
            cand.claims = [res.refined_text]
            cand.extractor_confidence = res.refined_confidence
            cand.lane = res.lane or cand.lane
            cand.provenance = {
                **(cand.provenance or {}),
                "pisha_refined": True,
                "pronoun_resolutions": res.pronoun_resolutions,
                "denoise_tags": res.denoise_tags,
                "pisha_reason": res.reason,
            }

        s.add(cand)
        s.commit()
        s.refresh(cand)
        logger.info(
            "披沙：候选【%s】精炼完成（status=%s, conf=%.2f）",
            cand.id,
            cand.status,
            cand.extractor_confidence,
        )
        return cand.model_dump(mode="json")

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def batch_refine_candidates(
    min_conf: float = 0.15,
    max_conf: float = 0.6,
    limit: int = 20,
    session: Session | None = None,
    *,
    principal=None,
) -> dict:
    """批量对处于模糊置信度区间的候选执行披沙提纯。

    归属收窄（票 .scratch/readside-gaps/02）：按 viewer 收窄查询（admin 全权）。
    """

    def _run(s: Session) -> dict:
        from lantai.services.candidate_service import _owner_scope

        scope = _owner_scope(principal)
        conditions = [
            MemoryCandidate.status == "pending_review",
            MemoryCandidate.extractor_confidence >= min_conf,
            MemoryCandidate.extractor_confidence <= max_conf,
        ]
        if scope is not None:
            conditions.append(scope)
        candidates = s.exec(
            select(MemoryCandidate)
            .where(*conditions)
            .order_by(MemoryCandidate.created_at.desc())
            .limit(limit)
        ).all()

        refined_count = 0
        rejected_count = 0
        engine = PishaEngine(session=s, principal=principal)
        for cand in candidates:
            raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
            context_str = str(cand.provenance) if cand.provenance else ""
            initial_conf = float(cand.extractor_confidence or 0.4)
            res = engine.refine_text(raw_text, context=context_str, initial_confidence=initial_conf)
            if res.llm_failed and not res.denoise_tags:
                continue
            if not res.is_valid or res.refined_confidence <= 0.0:
                cand.status = "rejected"
                cand.extractor_confidence = 0.0
                cand.provenance = {
                    **(cand.provenance or {}),
                    "pisha_rejected": True,
                    "pisha_reason": res.reason,
                }
                rejected_count += 1
            else:
                cand.summary = res.refined_text
                cand.claims = [res.refined_text]
                cand.extractor_confidence = res.refined_confidence
                cand.lane = res.lane or cand.lane
                cand.provenance = {
                    **(cand.provenance or {}),
                    "pisha_refined": True,
                    "pronoun_resolutions": res.pronoun_resolutions,
                    "denoise_tags": res.denoise_tags,
                    "pisha_reason": res.reason,
                }
                refined_count += 1
            s.add(cand)

        s.commit()
        return {
            "total_scanned": len(candidates),
            "refined": refined_count,
            "rejected": rejected_count,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def pisha_refine_service(
    text: str | None = None,
    candidate_id: str | None = None,
    context: str = "",
    entity_hints: dict[str, str] | None = None,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """统一披沙服务入口，支持纯文本提纯或候选条目落库提纯。"""
    if candidate_id:
        return refine_candidate_record(candidate_id, session=session, principal=principal)
    if text:
        return refine_memory_text(
            text=text, context=context, metadata={"entity_hints": entity_hints or {}}
        )
    raise ValueError("text 与 candidate_id 至少需提供其一")
