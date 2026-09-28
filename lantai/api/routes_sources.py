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
def add_source_route(req: SourceReq, ctx=Depends(get_current_user)):
    """创建来源（落归属，票 .scratch/readside-gaps/10）。

    `config` 是连接凭证（token/密码），无主来源等于把凭据公开挂墙上。
    """
    return add_source(req, principal=ctx)


@router.get("/sources")
def list_sources_route(ctx=Depends(get_current_user)):
    """列出来源（归属收窄 + config 脱敏，票 .scratch/readside-gaps/10）。"""
    return list_sources(principal=ctx)


@router.post("/ingest/run")
def ingest_run_route():
    return run_ingest()


@router.get("/candidates")
def list_candidates_route(status: str = "new", limit: int = 20, ctx=Depends(get_current_user)):
    """候选列表（`status` 过滤 + 归属收窄）。

    归属（票 .scratch/readside-gaps/07 修法口径 2）：此前一个身份都不取，
    把别人的候选 `summary` / `claims` / `contradictions` 明文连同 `id`
    一起吐出来。注意**这条不是 `/candidates/pending`**——那条 Ticket 02
    已修，读的是同一个 `MemoryCandidate` 表但走 `candidate_service`。
    """
    return list_candidates(status, limit, principal=ctx)


@router.delete("/documents/{document_id}")
def delete_document_route(document_id: str, principal=Depends(get_current_user)):
    """级联删除文档及其独占派生记忆。

    归属（票 `.scratch/readside-gaps/18`）：**两道校验管的不是同一行**——
    本路由这道校验 `RawDocument` 行的 `user_id` / `tenant_id`（票 04 既有），
    service 那道校验**级联目标记忆行**的归属。边的另一端是另一张表的另一行，
    前者够不着后者。别让后来人以为重复就删掉其中一道。

    403 语义在路由边界翻译（同 `routes_evolution._ok_or_raise`）：service 契约
    是 dict，形状不能动；HTTP 客户端看到的必须是 403 而不是 200 + ok:false。
    """
    from fastapi import HTTPException
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
    out = delete_document(document_id, principal=principal)
    if isinstance(out, dict) and out.get("ok") is False:
        reason = str(out.get("reason") or "operation failed")
        if reason.startswith("forbidden"):
            raise HTTPException(403, reason)
        raise HTTPException(404, reason)
    return out
