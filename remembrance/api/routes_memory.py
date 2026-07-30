import hashlib
from fastapi import APIRouter, HTTPException
from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.models.tables import RawDocument, MemoryCandidate, CoreMemoryBlock
from remembrance.models.schemas import AddMemoryReq
from remembrance.parsing.extractor import extract_candidate
from remembrance.storage.db import get_session

router = APIRouter()


@router.post("/add")
def add_memory(req: AddMemoryReq):
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    with get_session() as s:
        existed = s.exec(select(RawDocument)
                         .where(RawDocument.content_hash == h)).first()
        if existed:
            doc = existed
        else:
            doc = RawDocument(
                id=new_id("doc"), source_type=req.source_type,
                source_id=req.url or h[:12], url=req.url,
                title=req.title, authors=req.authors,
                content_hash=h, content=req.content,
                meta={"tags": req.tags},
            )
            s.add(doc); s.commit(); s.refresh(doc)

        data = extract_candidate(req.title, req.content)
        cand = MemoryCandidate(
            id=new_id("cand"), document_id=doc.id,
            topic=data["topic"] or req.tags,
            summary=data["summary"],
            claims=data["claims"], methods=data["methods"],
            constraints=data["constraints"], actions=data["actions"],
            extractor_confidence=data["extractor_confidence"],
        )
        s.add(cand); s.commit(); s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id}


@router.get("/core-memory")
def get_core_memory(namespace: str = "default"):
    with get_session() as s:
        blocks = s.exec(select(CoreMemoryBlock)
                        .where(CoreMemoryBlock.namespace == namespace)).all()
        return {"blocks": [b.model_dump(mode="json") for b in blocks]}


@router.put("/core-memory")
def put_core_memory(block: str, content: str, namespace: str = "default"):
    if block not in ("identity", "task", "policy"):
        raise HTTPException(400, "invalid block")
    with get_session() as s:
        row = s.exec(select(CoreMemoryBlock)
                     .where(CoreMemoryBlock.block == block,
                            CoreMemoryBlock.namespace == namespace)).first()
        if row:
            row.content = content; row.version += 1
        else:
            row = CoreMemoryBlock(id=new_id("core"), block=block,
                                  namespace=namespace, content=content)
        s.add(row); s.commit(); s.refresh(row)
        return row.model_dump(mode="json")
