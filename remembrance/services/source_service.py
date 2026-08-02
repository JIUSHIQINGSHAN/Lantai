"""来源与候选 service 层"""
from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.models.tables import Source, MemoryCandidate
from remembrance.models.schemas import SourceReq
from remembrance.storage import db
from remembrance.workers.ingest_worker import run_ingest_once


def add_source(req: SourceReq) -> dict:
    """创建来源。"""
    with db.get_session() as s:
        src = Source(id=new_id("src"), kind=req.kind,
                     config=req.config, enabled=req.enabled)
        s.add(src); s.commit(); s.refresh(src)
        return src.model_dump(mode="json")


def list_sources() -> dict:
    """列出所有来源。"""
    with db.get_session() as s:
        rows = s.exec(select(Source)).all()
        return {"sources": [r.model_dump(mode="json") for r in rows]}


def run_ingest() -> dict:
    """运行摄取 worker。"""
    run_ingest_once()
    return {"ok": True}


def list_candidates(status: str = "new", limit: int = 20) -> dict:
    """列出候选记忆。"""
    with db.get_session() as s:
        rows = s.exec(select(MemoryCandidate)
                      .where(MemoryCandidate.status == status)
                      .limit(limit)).all()
        return {"candidates": [r.model_dump(mode="json") for r in rows]}
