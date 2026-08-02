"""记忆写入与 CoreMemory service 层"""
import hashlib

from sqlmodel import select

from remembrance.core.ids import new_id
from remembrance.core.settings import settings
from remembrance.models.tables import RawDocument, MemoryCandidate, CoreMemoryBlock
from remembrance.models.schemas import AddMemoryReq
from remembrance.parsing.extractor import extract_candidate
from remembrance.parsing.fastpath import fastpath_check
from remembrance.ingestion.coalesce import get_coalesce_buffer
from remembrance.storage import db


def add_memory(req: AddMemoryReq) -> dict:
    """创建 RawDocument + MemoryCandidate。

    当 COALESCE_ENABLED=true 时走缓冲路径。
    fastpath 命中时直接写入（绕过 LLM 提取）。
    """
    # Fastpath 白名单直写——缓冲前判断
    fp = fastpath_check(req.content)
    if fp:
        return _create_candidate_direct(req, fp)

    # Coalesce 开关——true 时走缓冲
    if settings.COALESCE_ENABLED:
        buffer = get_coalesce_buffer()
        result = buffer.add(
            user_id="default", lane=req.lane,
            content=req.content, title=req.title,
        )
        if result.get("buffered"):
            return {"buffered": True, "count": result.get("count", 0)}
        # 缓冲冲刷——批量提取
        if result.get("flushed"):
            combined = result.get("combined_content", req.content)
            req_copy = req.model_copy()
            req_copy.content = combined
            return _create_candidate_with_extraction(req_copy)

    # 默认同步路径
    return _create_candidate_with_extraction(req)


def _create_candidate_direct(req: AddMemoryReq, fp_data: dict) -> dict:
    """fastpath 命中——直接创建 RawDocument + MemoryCandidate，不走 LLM"""
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    with db.get_session() as s:
        doc = RawDocument(
            id=new_id("doc"), source_type=req.source_type,
            source_id=req.url or h[:12], url=req.url,
            title=req.title, content=req.content, content_hash=h,
        )
        s.add(doc); s.commit(); s.refresh(doc)

        cand = MemoryCandidate(
            id=new_id("cand"), document_id=doc.id,
            topic=fp_data["topic"] or req.tags,
            summary=fp_data["summary"],
            claims=fp_data["claims"], methods=fp_data["methods"],
            constraints=fp_data["constraints"], actions=fp_data["actions"],
            extractor_confidence=fp_data["extractor_confidence"],
            lane=fp_data.get("lane", req.lane),
            status="fastpath",
        )
        s.add(cand); s.commit(); s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id, "fastpath": True}


def _create_candidate_with_extraction(req: AddMemoryReq) -> dict:
    """LLM 提取路径——原始逻辑"""
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    with db.get_session() as s:
        existed = s.exec(select(RawDocument)
                         .where(RawDocument.content_hash == h)).first()
        if existed:
            doc = existed
        else:
            doc = RawDocument(
                id=new_id("doc"), source_type=req.source_type,
                source_id=req.url or h[:12], url=req.url,
                title=req.title, content=req.content, content_hash=h,
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
            lane=req.lane,
        )
        s.add(cand); s.commit(); s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id}


def get_core_memory(namespace: str = "default") -> dict:
    """读取 CoreMemoryBlock 列表。"""
    with db.get_session() as s:
        blocks = s.exec(select(CoreMemoryBlock)
                        .where(CoreMemoryBlock.namespace == namespace)).all()
        return {"blocks": [b.model_dump(mode="json") for b in blocks]}


def put_core_memory(block: str, content: str, namespace: str = "default") -> dict:
    """创建或更新 CoreMemoryBlock。"""
    if block not in ("identity", "task", "policy"):
        raise ValueError("invalid block")
    with db.get_session() as s:
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
