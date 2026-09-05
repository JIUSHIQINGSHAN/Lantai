from sqlmodel import select

from lantai.core import scheduler as scheduler_mod
from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.provenance import PROVENANCE_PROMPT_EXTRACT, make_provenance
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.ingestion.registry import ADAPTERS
from lantai.models.tables import IngestJob, MemoryCandidate, RawDocument, Source
from lantai.parsing.extractor import extract_candidate
from lantai.storage import db


def run_ingest_once():
    with db.get_session() as s:
        sources = s.exec(select(Source).where(Source.enabled)).all()
    for src in sources:
        job = IngestJob(id=new_id("job"), source_id=src.id,
                        status="running", started_at=utcnow())
        with db.get_session() as s:
            s.add(job); s.commit(); s.refresh(job)

        try:
            adapter = ADAPTERS[src.kind]
            docs = adapter.fetch(src.config)
            new_docs, new_cands = 0, 0
            paper_doc_ids: list[str] = []
            with db.get_session() as s:
                for d in docs:
                    exists = s.exec(select(RawDocument)
                                    .where(RawDocument.content_hash == d.content_hash)).first()
                    if exists:
                        continue
                    s.add(d); s.flush(); new_docs += 1

                    # 参数建议：仅 paper 类型入队（RSS article 首期不参与）
                    if d.source_type == "paper":
                        paper_doc_ids.append(d.id)

                    data = extract_candidate(d.title, d.content)
                    cand = MemoryCandidate(
                        id=new_id("cand"),
                        document_id=d.id,
                        topic=data["topic"], summary=data["summary"],
                        claims=data["claims"], methods=data["methods"],
                        constraints=data["constraints"], actions=data["actions"],
                        extractor_confidence=data["extractor_confidence"],
                        provenance=make_provenance(PROVENANCE_PROMPT_EXTRACT),
                    )
                    s.add(cand); new_cands += 1
                s.commit()

            # 论文已落库成功，幂等入队（在摄入事务提交后，避免持锁等网络）
            from lantai.parameters.queue import enqueue_paper_for_param_advice
            for pid in paper_doc_ids:
                try:
                    enqueue_paper_for_param_advice(pid)
                except Exception:
                    logger.exception("enqueue paper for param advice failed: %s", pid)

            # 质量信号落库（方向一）：doc.meta 中携带的解析草稿 → paper_quality_signal
            if paper_doc_ids and settings.PAPER_SIGNAL_ENABLED:
                try:
                    from lantai.models.tables import RawDocument as RD
                    from lantai.parameters.paper_signals import QualitySignalDraft
                    from lantai.parameters.signal_service import upsert_from_draft
                    with db.get_session() as s:
                        docs = s.exec(select(RD).where(RD.id.in_(paper_doc_ids))).all()
                        drafts = {d.id: d.meta.get("quality_signal") for d in docs}
                    for pid, payload in drafts.items():
                        if payload:
                            upsert_from_draft(
                                pid, QualitySignalDraft.model_validate(payload))
                        else:
                            # 无草稿（解析失败）→ 保底 tier D
                            upsert_from_draft(pid, QualitySignalDraft(arxiv_id=pid))
                except Exception:
                    logger.exception("quality signal upsert failed")

            with db.get_session() as s:
                job = s.get(IngestJob, job.id)
                job.status = "done"
                job.finished_at = utcnow()
                job.stats = {"docs": new_docs, "candidates": new_cands}
                src2 = s.get(Source, src.id); src2.last_fetched_at = utcnow()
                s.add(job); s.add(src2); s.commit()
            logger.info("ingest src=%s docs=%d cand=%d", src.id, new_docs, new_cands)
        except Exception as e:
            logger.exception("ingest failed for %s", src.id)
            with db.get_session() as s:
                job = s.get(IngestJob, job.id)
                job.status = "failed"; job.error = str(e)
                job.finished_at = utcnow()
                s.add(job); s.commit()

    # 本轮摄入提交后触发一次参数建议（窗口不满足则空转）
    try:
        from lantai.workers.param_advice_worker import run_param_advice_once
        run_param_advice_once()
    except Exception:
        logger.exception("param advice trigger failed")
    scheduler_mod.record_run("ingest")


def run_coalesce_idle():
    """coalesce 空闲冲刷消费：冲刷出的批量内容持久化，不静默丢弃。"""
    from lantai.ingestion.coalesce import get_coalesce_buffer
    from lantai.models.schemas import AddMemoryReq
    from lantai.services import memory_service as ms

    buffer = get_coalesce_buffer()
    for result in buffer.check_idle():
        if not result.get("flushed"):
            continue
        combined = result.get("combined_content", "")
        key = result.get("key", "default:general")
        lane = key.split(":", 1)[-1]
        try:
            req = AddMemoryReq(title="coalesced", content=combined, lane=lane)
            ms._create_candidate_with_extraction(req)
        except Exception:
            logger.exception("coalesce flush persist failed for key=%s", key)
            # 持久化失败：锁内恢复该批消息（不触发二次冲刷），下轮再试
            buffer.requeue(key, result.get("items", []))
    scheduler_mod.record_run("coalesce")
