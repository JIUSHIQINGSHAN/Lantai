"""
参数建议 worker——批量窗口触发 LLM 建议生成（辅助模式，绝不自动应用）。

流程：recover stale → claim batch → generate（无 fallback）→ 入库 pending 建议
      → 网络失败转 retry（≤3 次）→ 非法输出/abstain 标记 consumed（不重试）
"""
import hashlib

from sqlmodel import select

from remembrance.core import scheduler as scheduler_mod
from remembrance.core.ids import new_id
from remembrance.core.logger import logger
from remembrance.core.settings import settings
from remembrance.core.time import utcnow
from remembrance.models.tables import ParamSuggestion
from remembrance.parameters.advisor import generate_param_advice
from remembrance.parameters.queue import (
    claim_advice_batch,
    finish_run_abstained,
    finish_run_suggested,
    mark_papers_consumed,
    mark_papers_retry,
    recover_stale_claims,
)
from remembrance.parameters.registry import canonical_json, get_registry_version
from remembrance.parameters.schemas import AbstainPayload, SuggestPayload
from remembrance.parameters.validation import apply_validated_changes, snapshot_hash
from remembrance.storage import db


def _fingerprint(source_ids: list[str], base_hash: str,
                 after_hash: str) -> str:
    digest = hashlib.sha256(
        canonical_json(sorted(source_ids)).encode("utf-8")
        + base_hash.encode("utf-8") + after_hash.encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def create_suggestion_record(run_id: str, payload: SuggestPayload,
                             base_snapshot: dict,
                             source_ids: list[str]) -> bool:
    """校验通过的建议入库（pending）。fingerprint 去重，重复则跳过。"""
    after = apply_validated_changes(base_snapshot, payload.changes)
    base_hash = snapshot_hash(base_snapshot)
    after_hash = snapshot_hash(after)
    fp = _fingerprint(source_ids, base_hash, after_hash)

    with db.get_session() as s:
        dup = s.exec(select(ParamSuggestion).where(
            ParamSuggestion.fingerprint == fp)).first()
        if dup:
            logger.info("duplicate param suggestion skipped: %s", fp[:16])
            return False
        s.add(ParamSuggestion(
            id=new_id("psg"),
            run_id=run_id,
            status="pending",
            confidence=payload.confidence,
            title=payload.title,
            summary=payload.summary,
            rationale=payload.rationale,
            expected_benefit=payload.expected_benefit,
            risk_notes=payload.risk_notes,
            validation_plan=payload.validation_plan,
            source_document_ids=source_ids,
            evidence=[e.model_dump() for e in payload.evidence],
            changes=[c.model_dump() for c in payload.changes],
            before_snapshot=base_snapshot,
            after_snapshot=after,
            base_snapshot_hash=base_hash,
            registry_version=get_registry_version(),
            fingerprint=fp,
        ))
        s.commit()
        return True


def run_param_advice_once() -> None:
    if not settings.PARAM_ADVICE_ENABLED:
        scheduler_mod.record_run("param_advice")
        return

    recover_stale_claims()
    batch = claim_advice_batch()
    if batch is None:
        scheduler_mod.record_run("param_advice")
        return

    run_id = batch["run_id"]
    paper_ids = batch["paper_ids"]
    papers = batch["papers"]
    base_snapshot = batch["base_snapshot"]

    result = generate_param_advice(papers, base_snapshot)
    if not result["ok"]:
        code = result["error_code"]
        if code == "llm_error":
            # 网络失败：转 retry（论文级重试，上限在 queue 内控制）
            mark_papers_retry(paper_ids, run_id, code)
            logger.warning("param advice llm_error, papers -> retry")
        elif code == "disabled":
            mark_papers_retry(paper_ids, run_id, code)
        else:  # validation_error：非法输出不重试，避免反复请求
            mark_papers_consumed(paper_ids, run_id)
            logger.info("param advice validation_error, papers -> consumed")
        scheduler_mod.record_run("param_advice")
        return

    payload = result["payload"]
    if isinstance(payload, AbstainPayload):
        mark_papers_consumed(paper_ids, run_id)
        finish_run_abstained(run_id)
        logger.info("param advice abstained: %s", payload.reason[:80])
        scheduler_mod.record_run("param_advice")
        return

    created = create_suggestion_record(run_id, payload, base_snapshot,
                                       [p["source_document_id"]
                                        for p in papers])
    mark_papers_consumed(paper_ids, run_id)
    finish_run_suggested(run_id)
    logger.info("param advice suggested: %s (created=%s)",
                payload.title, created)
    scheduler_mod.record_run("param_advice")
