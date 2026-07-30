from fastapi import APIRouter
from sqlmodel import select
from remembrance.core.ids import new_id
from remembrance.models.tables import Source, MemoryCandidate
from remembrance.models.schemas import SourceReq
from remembrance.storage import db
from remembrance.workers.ingest_worker import run_ingest_once

router = APIRouter()


@router.post("/sources")
def add_source(req: SourceReq):
    with db.get_session() as s:
        src = Source(id=new_id("src"), kind=req.kind,
                     config=req.config, enabled=req.enabled)
        s.add(src); s.commit(); s.refresh(src)
        return src.model_dump(mode="json")


@router.get("/sources")
def list_sources():
    with db.get_session() as s:
        rows = s.exec(select(Source)).all()
        return {"sources": [r.model_dump(mode="json") for r in rows]}


@router.post("/ingest/run")
def ingest_run():
    run_ingest_once()
    return {"ok": True}


@router.get("/candidates")
def list_candidates(status: str = "new", limit: int = 20):
    with db.get_session() as s:
        rows = s.exec(select(MemoryCandidate)
                      .where(MemoryCandidate.status == status)
                      .limit(limit)).all()
        return {"candidates": [r.model_dump(mode="json") for r in rows]}
