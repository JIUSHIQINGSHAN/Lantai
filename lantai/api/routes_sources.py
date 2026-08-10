from fastapi import APIRouter

from lantai.models.schemas import SourceReq
from lantai.services.source_service import (
    add_source,
    list_sources,
    run_ingest,
    list_candidates,
)

router = APIRouter()


@router.post("/sources")
def add_source_route(req: SourceReq):
    return add_source(req)


@router.get("/sources")
def list_sources_route():
    return list_sources()


@router.post("/ingest/run")
def ingest_run_route():
    return run_ingest()


@router.get("/candidates")
def list_candidates_route(status: str = "new", limit: int = 20):
    return list_candidates(status, limit)
