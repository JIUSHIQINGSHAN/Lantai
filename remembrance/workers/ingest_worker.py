from sqlmodel import select
from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.core.logger import logger
from remembrance.ingestion.registry import ADAPTERS
from remembrance.parsing.extractor import extract_candidate
from remembrance.models.tables import (Source, IngestJob, RawDocument,
                                       MemoryCandidate)
from remembrance.storage import db
from remembrance.core import scheduler as scheduler_mod


def run_ingest_once():
    with db.get_session() as s:
        sources = s.exec(select(Source).where(Source.enabled == True)).all()
    for src in sources:
        job = IngestJob(id=new_id("job"), source_id=src.id,
                        status="running", started_at=utcnow())
        with db.get_session() as s:
            s.add(job); s.commit(); s.refresh(job)

        try:
            adapter = ADAPTERS[src.kind]
            docs = adapter.fetch(src.config)
            new_docs, new_cands = 0, 0
            with db.get_session() as s:
                for d in docs:
                    exists = s.exec(select(RawDocument)
                                    .where(RawDocument.content_hash == d.content_hash)).first()
                    if exists:
                        continue
                    s.add(d); s.flush(); new_docs += 1

                    data = extract_candidate(d.title, d.content)
                    cand = MemoryCandidate(
                        id=new_id("cand"),
                        document_id=d.id,
                        topic=data["topic"], summary=data["summary"],
                        claims=data["claims"], methods=data["methods"],
                        constraints=data["constraints"], actions=data["actions"],
                        extractor_confidence=data["extractor_confidence"],
                    )
                    s.add(cand); new_cands += 1
                s.commit()

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
    scheduler_mod.record_run("ingest")
