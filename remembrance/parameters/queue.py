"""
论文队列——批量窗口触发（类比潮波并忆：攒批再处理，避免单篇噪声）。

状态机：new → processing → (retry | consumed | dead)
- new/retry      ：可被领取
- processing     ：已领取，LLM 处理中（卡死超时后 recover 回 retry）
- consumed       ：已处理完毕（产出建议 / 合法 abstain / 非法输出，不再重试）
- dead           ：网络重试耗尽
"""
from datetime import timedelta, timezone

from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.core.logger import logger
from remembrance.core.settings import settings
from remembrance.core.time import utcnow
from remembrance.models.tables import (
    ParamAdviceRun,
    ParamAdvicePaper,
    RawDocument,
)
from remembrance.parameters.registry import default_snapshot, get_registry_version
from remembrance.parameters.validation import snapshot_hash
from remembrance.storage import db


def _ensure_aware(dt):
    """SQLite 读回的 datetime 是 naive；统一视为 UTC（项目标准处理）。"""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def enqueue_paper_for_param_advice(raw_document_id: str) -> bool:
    """
    论文入队（幂等：raw_document_id 唯一）。
    返回 True 表示新入队。仅在论文成功落库后调用。
    """
    with db.get_session() as s:
        exists = s.exec(select(ParamAdvicePaper).where(
            ParamAdvicePaper.raw_document_id == raw_document_id)).first()
        if exists:
            return False
        s.add(ParamAdvicePaper(
            id=new_id("pap"),
            raw_document_id=raw_document_id,
            state="new",
            available_at=utcnow(),
        ))
        s.commit()
        return True


def _candidate_papers(session) -> list[ParamAdvicePaper]:
    return list(session.exec(select(ParamAdvicePaper).where(
        ParamAdvicePaper.state.in_(["new", "retry"]),
        ParamAdvicePaper.attempt_count < settings.PARAM_ADVICE_MAX_RETRIES,
    ).order_by(ParamAdvicePaper.available_at.asc())).all())


def claim_advice_batch() -> dict | None:
    """
    领取一批论文：
    - 触发条件：未处理数 >= MIN_PAPERS，或最老论文等待 >= MAX_WAIT_DAYS
    - 单批最多 MAX_BATCH_SIZE
    - 返回 {run, papers}；不满足窗口返回 None
    """
    with db.get_session() as s:
        papers = _candidate_papers(s)
        if not papers:
            return None
        now = utcnow()
        enough = len(papers) >= settings.PARAM_ADVICE_MIN_PAPERS
        oldest_wait = (now - _ensure_aware(papers[0].available_at)).days \
            >= settings.PARAM_ADVICE_MAX_WAIT_DAYS
        if not enough and not oldest_wait:
            return None

        batch = papers[:settings.PARAM_ADVICE_MAX_BATCH_SIZE]
        ids = [p.id for p in batch]
        raw_ids = [p.raw_document_id for p in batch]

        run = ParamAdviceRun(
            id=new_id("par"),
            status="processing",
            source_document_ids=raw_ids,
            base_snapshot=default_snapshot(),
            base_snapshot_hash=snapshot_hash(default_snapshot()),
            registry_version=get_registry_version(),
        )
        s.add(run)
        s.flush()
        run_id = run.id
        base_snapshot = dict(run.base_snapshot)

        for p in batch:
            p.state = "processing"
            p.claimed_at = now
            p.run_id = run_id
            p.updated_at = now
            s.add(p)
        s.commit()

        # commit 后再取正文，避免长事务
        docs = s.exec(select(RawDocument).where(
            RawDocument.id.in_(raw_ids))).all()

    papers_payload = [
        {"source_document_id": d.id, "title": d.title,
         "source_url": d.url, "content": d.content}
        for d in docs
    ]
    logger.info("param advice claimed batch: run=%s papers=%d",
                run_id, len(papers_payload))
    return {"run_id": run_id, "papers": papers_payload,
            "paper_ids": ids, "base_snapshot": base_snapshot}


def recover_stale_claims() -> int:
    """processing 卡死超时（120 分钟）恢复为 retry。返回恢复数。"""
    stale_before = utcnow() - timedelta(
        minutes=settings.PARAM_ADVICE_PROCESSING_STALE_MINUTES)
    recovered = 0
    with db.get_session() as s:
        stale = list(s.exec(select(ParamAdvicePaper).where(
            ParamAdvicePaper.state == "processing",
            ParamAdvicePaper.claimed_at.is_not(None),
            ParamAdvicePaper.claimed_at < stale_before,
        )).all())
        for p in stale:
            claimed = _ensure_aware(p.claimed_at)
            if claimed >= stale_before:
                continue
            p.state = "retry"
            p.attempt_count += 1
            p.last_error_code = "stale_recovery"
            p.updated_at = utcnow()
            s.add(p)
            recovered += 1
        if stale:
            s.commit()
            logger.warning("recovered %d stale processing claims", recovered)
    return recovered


def mark_papers_consumed(paper_ids: list, run_id: str | None) -> None:
    """处理完成（产出建议/合法 abstain/非法输出）：标记 consumed，不再重试。"""
    with db.get_session() as s:
        for pid in paper_ids:
            row = s.get(ParamAdvicePaper, pid)
            if row:
                row.state = "consumed"
                row.consumed_at = utcnow()
                row.updated_at = utcnow()
                s.add(row)
        if run_id:
            run = s.get(ParamAdviceRun, run_id)
            if run:
                run.finished_at = utcnow()
                s.add(run)
        s.commit()


def mark_papers_retry(paper_ids: list, run_id: str | None,
                      error_code: str) -> None:
    """网络失败：转 retry（attempt+1）；超限转 dead。"""
    with db.get_session() as s:
        for pid in paper_ids:
            row = s.get(ParamAdvicePaper, pid)
            if not row:
                continue
            row.attempt_count += 1
            row.last_error_code = error_code
            if row.attempt_count >= settings.PARAM_ADVICE_MAX_RETRIES:
                row.state = "dead"
            else:
                row.state = "retry"
            row.updated_at = utcnow()
            s.add(row)
        if run_id:
            run = s.get(ParamAdviceRun, run_id)
            if run:
                run.status = "failed"
                run.error_code = error_code
                run.finished_at = utcnow()
                s.add(run)
        s.commit()


def finish_run_suggested(run_id: str) -> None:
    with db.get_session() as s:
        run = s.get(ParamAdviceRun, run_id)
        if run:
            run.status = "suggested"
            run.finished_at = utcnow()
            s.add(run)
            s.commit()


def finish_run_abstained(run_id: str) -> None:
    with db.get_session() as s:
        run = s.get(ParamAdviceRun, run_id)
        if run:
            run.status = "abstained"
            run.finished_at = utcnow()
            s.add(run)
            s.commit()
