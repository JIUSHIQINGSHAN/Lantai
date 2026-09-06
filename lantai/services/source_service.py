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


from lantai.models.tables import RawDocument, DocumentChunk, MemoryCandidate, MemoryEdge, MemoryItem
from lantai.evolution.promoter import delete_memory

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
        candidates = s.exec(select(MemoryCandidate).where(MemoryCandidate.document_id == document_id)).all()
        for cand in candidates:
            # We skip deleting proposals specifically here to save time, they will be orphaned or we can let them be
            s.delete(cand)
            
        # 4. Find edges originating from this document
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.source_memory_id == document_id)).all()
        mem_ids_to_check = set([e.target_memory_id for e in edges])
        
        for e in edges:
            s.delete(e)
            
        s.commit() # Commit edge deletions first
        
        # 5. Check those target memories. If they no longer have any incoming doc edges, delete them.
        deleted_mems = 0
        for mem_id in mem_ids_to_check:
            # Check if there are other doc sources for this memory
            remaining_edges = s.exec(select(MemoryEdge).where(MemoryEdge.target_memory_id == mem_id)).all()
            doc_sources = [e.source_memory_id for e in remaining_edges if e.source_memory_id.startswith("doc_")]
            if not doc_sources:
                # No more documents supporting this memory, we can delete it
                delete_memory(mem_id)
                deleted_mems += 1
                
        return {"ok": True, "deleted_document_id": document_id, "deleted_memories": deleted_mems}
