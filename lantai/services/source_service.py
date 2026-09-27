"""来源与候选 service 层"""

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.models.schemas import SourceReq
from lantai.models.tables import MemoryCandidate, Source
from lantai.storage import db
from lantai.workers.ingest_worker import run_ingest_once


def add_source(req: SourceReq) -> dict:
    """创建来源。"""
    with db.get_session() as s:
        src = Source(id=new_id("src"), kind=req.kind, config=req.config, enabled=req.enabled)
        s.add(src)
        s.commit()
        s.refresh(src)
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


def list_candidates(status: str = "new", limit: int = 20, principal=None) -> dict:
    """列出候选记忆。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 2）：此前一个身份都不取，
    `select(MemoryCandidate).where(status==status)` 全表捞，把别人的
    `summary` / `claims` / `contradictions` 明文连同 `id` 一起吐给任何持 key 者
    （第三轮探测 `OBSERVED3.txt` 实证）。`MemoryCandidate` **有**完整归属四元组
    （`tables.py:73-78`），所以这里能直接按 viewer 收窄，不需要过渡推导。

    口径与 Ticket 02 的 `list_pending_candidates` **逐字一致**——复用
    `work_item_service._owner_scope` 单一判据：admin/system 全权；
    否则严格 `user_id = viewer`，**NULL 老行不可见**。候选正文是高敏字段，
    这是「宁 miss 不脏写」的选择：宁可少列，不可错放。
    （真实库 37 行候选全部 `user_id='default'`，单用户部署下列的就是自己的。）

    `principal=None`（内部 CLI/worker/MCP）不加过滤，与改动前逐字一致。
    """
    from lantai.services.work_item_service import _owner_scope

    with db.get_session() as s:
        q = select(MemoryCandidate).where(MemoryCandidate.status == status)
        scope = _owner_scope(MemoryCandidate.user_id, principal)
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q.limit(limit)).all()
        return {"candidates": [r.model_dump(mode="json") for r in rows]}


from lantai.evolution.promoter import delete_memory
from lantai.models.tables import DocumentChunk, MemoryEdge, MemoryItem, RawDocument


def delete_document(document_id: str) -> dict:
    """Cascade delete a document and its exclusively derived memories."""
    with db.get_session() as s:
        # 1. Delete RawDocument
        doc = s.get(RawDocument, document_id)
        if doc:
            s.delete(doc)

        # 2. Delete DocumentChunk
        chunks = s.exec(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all()
        for chunk in chunks:
            s.delete(chunk)

        # 3. Find MemoryCandidates
        candidates = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.document_id == document_id)
        ).all()
        for cand in candidates:
            # We skip deleting proposals specifically here to save time, they will be orphaned or we can let them be
            s.delete(cand)

        # 4. Find edges originating from this document
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.source_memory_id == document_id)).all()
        mem_ids_to_check = set([e.target_memory_id for e in edges])

        for e in edges:
            s.delete(e)

        s.commit()  # Commit edge deletions first

        # 5. Check those target memories. If they no longer have any incoming doc edges, delete them.
        deleted_mems = 0
        for mem_id in mem_ids_to_check:
            # Check if there are other doc sources for this memory
            remaining_edges = s.exec(
                select(MemoryEdge).where(MemoryEdge.target_memory_id == mem_id)
            ).all()
            doc_sources = [
                e.source_memory_id for e in remaining_edges if e.source_memory_id.startswith("doc_")
            ]
            if not doc_sources:
                # No more documents supporting this memory, we can delete it
                delete_memory(mem_id)
                deleted_mems += 1

        return {"ok": True, "deleted_document_id": document_id, "deleted_memories": deleted_mems}
