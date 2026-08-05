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
from remembrance.parameters.schemas import SuggestPayload
from remembrance.parameters.validation import apply_validated_changes, snapshot_hash
from remembrance.storage import db


def _fingerprint(source_ids: list[str], base_hash: str,
                 after_hash: str) -> str:
    digest = hashlib.sha256(
        canonical_json(sorted(source_ids)).encode("utf-8")
        + base_hash.encode("utf-8") + after_hash.encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _create_contradiction_report(run_id: str, item) -> None:
    """矛盾条目落库（方向四）：只可 acknowledge/close，禁止 apply。"""
    from remembrance.parameters.trust_models import ParamContradictionReport
    with db.get_session() as s:
        s.add(ParamContradictionReport(
            id=new_id("pcr"),
            run_id=run_id,
            param_key=item.param_key,
            nature=item.nature,
            side_a=item.side_a.model_dump(),
            side_b=item.side_b.model_dump(),
            scope_note=item.scope_note,
            status="open",
        ))
        s.commit()


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

    # 加载质量信号（方向一）：缺失论文按无信号处理（校验时按 ineligible）
    from remembrance.parameters.signal_service import load_signal_views
    source_ids = [p["source_document_id"] for p in papers]
    signal_views = load_signal_views(source_ids)

    result = generate_param_advice(papers, base_snapshot,
                                   views=signal_views)
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

    payload = result["payload"]  # {suggestions: [...], contradictions: [...]}

    # 矛盾报告落库（方向四）：矛盾参数只可 acknowledge/close，接口层禁止 apply
    for c in payload.get("contradictions", []):
        _create_contradiction_report(run_id, c)

    # 逐条建议入库（fingerprint 去重）
    created = 0
    for item in payload.get("suggestions", []):
        try:
            if create_suggestion_record(run_id, item["suggestion"],
                                        base_snapshot,
                                        [p["source_document_id"]
                                         for p in papers]):
                created += 1
        except Exception:
            logger.exception("create suggestion record failed, skip")

    mark_papers_consumed(paper_ids, run_id)
    if created > 0:
        finish_run_suggested(run_id)
    else:
        finish_run_abstained(run_id)
    logger.info("param advice batch done: suggestions=%d contradictions=%d",
                created, len(payload.get("contradictions", [])))
    scheduler_mod.record_run("param_advice")
