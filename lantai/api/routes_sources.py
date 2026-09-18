from fastapi import APIRouter, Depends

from lantai.core.auth import get_current_user
from lantai.models.schemas import SourceReq
from lantai.services.source_service import (
    add_source,
    delete_document,
    list_candidates,
    list_sources,
    run_ingest,
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


@router.delete("/documents/{document_id}")
def delete_document_route(document_id: str, principal=Depends(get_current_user)):
    """级联删除文档及其独占派生记忆（含归属校验，P0 票04）"""
    from sqlmodel import select

    from lantai.core.acl import ensure_can_delete
    from lantai.models.tables import RawDocument
    from lantai.storage.db import get_session

    with get_session() as s:
        doc = s.exec(select(RawDocument).where(RawDocument.id == document_id)).first()
        if doc:
            ensure_can_delete(
                principal, resource_user_id=doc.user_id, resource_tenant_id=doc.tenant_id
            )
    return delete_document(document_id)
