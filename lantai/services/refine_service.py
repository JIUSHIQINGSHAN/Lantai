"""披沙（ADR-0077）：候选记忆递归提纯去噪（Pisha）核心服务。

基于认知层 PishaEngine 统一封装：
1. refine_memory_text: LLM 与确定性启发式多轨提纯（纯函数，异常时宁 miss 不脏写降级）；
2. refine_candidate_record: 针对单条候选记录进行精炼并更新 DB；
3. batch_refine_candidates: 针对模糊区间候选批量披沙提纯。

票 58 收口：limit 边界（负值/0/超上界拒）、改写前原件备份（provenance.pisha_original）、
LLM 产物过獬豸（带毒不覆盖原文留痕交裁决）、已精炼幂等闸（不再烧 LLM）。落库写面
统一走 `_apply_refine_result` 单一真源——备份/毒闸/标记三件事不在两条路径各写一遍。
"""

from typing import Any

from sqlalchemy import func, or_
from sqlmodel import Session, select

from lantai.cognition.pisha import PishaEngine
from lantai.core.logger import logger
from lantai.models.tables import MemoryCandidate
from lantai.storage import db

# 批量精炼上界（票 58）：对齐既有逐条 LLM 批量面口径（routes_param_advice le=100）。
# 逐条烧 LLM 的批量面必须双界——下界防负值/0（SQLite 负 LIMIT = 无 LIMIT 全表选中），
# 上界防一次请求烧穿 LLM 预算。
REFINE_BATCH_LIMIT_MAX = 100


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


def _apply_refine_result(cand: MemoryCandidate, res) -> str:
    """精炼结果落库单一真源（票 58）：返回 "refined" | "rejected" | "poisoned"。

    refined：先备份原件（provenance.pisha_original——summary/claims 原地覆盖后唯一
    可回滚位）再覆盖；LLM 产物过獬豸，带毒不覆盖原文、provenance 记 pisha_poisoned
    留痕（宁 miss 不脏写 + 不静默丢——候选保持当前状态交用户裁决，AGENTS v0.5 纪律）。
    """
    # LLM 产物过獬豸（票 58）：refined_text 会落 cand.summary 进库内所有渲染面
    # （context 目录/to_prompt/樊篱包裹面），带毒即污染面扩大，与写入面同闸口径。
    try:
        from lantai.security.injection_guard import assert_no_prompt_injection

        assert_no_prompt_injection(res.refined_text)
    except ValueError as exc:
        logger.warning("披沙:候选【%s】LLM 产物带毒拒绝落库（票 58）：%s", cand.id, exc)
        cand.provenance = {
            **(cand.provenance or {}),
            "pisha_poisoned": True,
            "pisha_reason": str(exc),
        }
        return "poisoned"

    if not res.is_valid or res.refined_confidence <= 0.0:
        cand.status = "rejected"
        cand.extractor_confidence = 0.0
        cand.provenance = {
            **(cand.provenance or {}),
            "pisha_rejected": True,
            "pisha_reason": res.reason,
        }
        return "rejected"

    cand.provenance = {
        **(cand.provenance or {}),
        "pisha_original": {"summary": cand.summary, "claims": list(cand.claims or [])},
        "pisha_refined": True,
        "pronoun_resolutions": res.pronoun_resolutions,
        "denoise_tags": res.denoise_tags,
        "pisha_reason": res.reason,
    }
    cand.summary = res.refined_text
    cand.claims = [res.refined_text]
    cand.extractor_confidence = res.refined_confidence
    cand.lane = res.lane or cand.lane
    return "refined"


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

        # 已精炼幂等闸（票 58）：pisha_refined 已在案则不再烧 LLM——重复提交返回
        # 当前态而非迭代改写（防额度烧穿与注入迭代漂移）。rejected 不挡（原件仍在，
        # 用户可对改进后的 LLM 重试）。
        if (cand.provenance or {}).get("pisha_refined"):
            logger.info("披沙:候选【%s】已精炼过，幂等返回不再调 LLM（票 58）", cand.id)
            return cand.model_dump(mode="json")

        engine = PishaEngine()
        raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
        context_str = str(cand.provenance) if cand.provenance else ""
        initial_conf = float(cand.extractor_confidence or 0.4)

        res = engine.refine_text(raw_text, context=context_str, initial_confidence=initial_conf)
        if res.llm_failed and not res.denoise_tags:
            logger.warning("披沙:候选【%s】LLM 不可用且无规则修剪,保持原样待重试", cand.id)
            return cand.model_dump(mode="json")

        _apply_refine_result(cand, res)

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
    limit 边界（票 58）：负值/0/超上界 ValueError 拒绝——负值在 SQLite 等于无
    LIMIT（全表选中进批量改写）；上界防一次请求烧穿逐条 LLM 预算。
    """
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > REFINE_BATCH_LIMIT_MAX
    ):
        raise ValueError(
            f"limit 必须是 1..{REFINE_BATCH_LIMIT_MAX} 的正整数（票 58），得到 {limit!r}"
        )

    def _run(s: Session) -> dict:
        from lantai.services.candidate_service import _owner_scope

        scope = _owner_scope(principal)
        # 已精炼排除（票 58）：pisha_refined 在案的候选不再入批——refine 后置信度
        # 可留在模糊带内（红测用带内 0.5 锁定），光靠带宽排除不封口。裸
        # json_extract 而非 SQLAlchemy JSON 路径的 .is_(None)：后者编译成
        # JSON_QUOTE(JSON_EXTRACT(...))，而 SQLite 的 JSON_QUOTE(NULL) 返回字符串
        # 'null'（非 SQL NULL），键缺失行会永远不匹配、整批扫空。键缺失（SQL
        # NULL）= 未精炼，必须放行；true 提取为 1。
        prov_refined = func.json_extract(MemoryCandidate.provenance, "$.pisha_refined")
        conditions = [
            MemoryCandidate.status == "pending_review",
            MemoryCandidate.extractor_confidence >= min_conf,
            MemoryCandidate.extractor_confidence <= max_conf,
            or_(prov_refined.is_(None), prov_refined != 1),
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
        poisoned_count = 0
        engine = PishaEngine()
        for cand in candidates:
            raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
            context_str = str(cand.provenance) if cand.provenance else ""
            initial_conf = float(cand.extractor_confidence or 0.4)
            res = engine.refine_text(raw_text, context=context_str, initial_confidence=initial_conf)
            if res.llm_failed and not res.denoise_tags:
                continue
            outcome = _apply_refine_result(cand, res)
            if outcome == "refined":
                refined_count += 1
            elif outcome == "rejected":
                rejected_count += 1
            else:
                poisoned_count += 1
            s.add(cand)

        s.commit()
        return {
            "total_scanned": len(candidates),
            "refined": refined_count,
            "rejected": rejected_count,
            "poisoned": poisoned_count,
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
