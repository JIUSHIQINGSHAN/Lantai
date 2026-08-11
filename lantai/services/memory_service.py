"""记忆写入与 CoreMemory service 层"""
import hashlib

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import RawDocument, MemoryCandidate, CoreMemoryBlock, MemoryItem, MemoryProposal
from lantai.models.enums import MemoryTier
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.llm.client import embed
from lantai.core.provenance import (
    PROVENANCE_PROMPT_EXTRACT, PROVENANCE_PROMPT_FASTPATH_DIRECT, make_provenance)
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage.fts import sync_fts
from lantai.parsing.extractor import extract_candidate
from lantai.parsing.fastpath import fastpath_check
from lantai.ingestion.coalesce import get_coalesce_buffer
from lantai.gate.dedup import find_similar
from lantai.memory.decay_class import DECAY_CLASS_HALFLIFE
from lantai.evolution.promoter import _make_checkpoint
from lantai.storage import db
from lantai.storage.vector_store import get_vector_store

vector_store = get_vector_store()


def _apply_dedup(s, title: str, content: str, lane: str) -> dict | None:
    """candidate 创建前的三态去重。返回 None 表示 insert（继续正常建候选）。"""
    try:
        vec_results = vector_store.search(content, top_k=1)
        if not isinstance(vec_results, list):
            return None
        action, target, sim = find_similar(s, vec_results)
    except Exception:
        return None

    if action == "insert" or target is None:
        return None
    if action == "merge":
        target.last_used_at = utcnow()
        target.importance = min(1.0, target.importance + 0.1)
        s.add(target)
        s.commit()
        return {"dedup_action": "merge", "target_memory_id": target.id, "similarity": round(sim, 4)}
    prop = MemoryProposal(
        id=new_id("prop"),
        target_memory_id=target.id,
        proposal_type="update",
        proposed_patch={"title": title, "content": content, "lane": lane},
        confidence=round(sim, 4),
        status="pending",
    )
    s.add(prop)
    s.commit()
    s.refresh(prop)
    return {"dedup_action": "update", "target_memory_id": target.id,
            "proposal_id": prop.id, "similarity": round(sim, 4)}


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
        dedup_result = _apply_dedup(s, req.title, req.content, req.lane)
        if dedup_result is not None:
            return dedup_result
        doc = RawDocument(
            id=new_id("doc"), source_type=req.source_type,
            source_id=req.url or h[:12], url=req.url,
            title=req.title, content=req.content, content_hash=h,
            meta=req.metadata,
        )
        s.add(doc); s.commit(); s.refresh(doc)

        cand = MemoryCandidate(
            id=new_id("cand"), document_id=doc.id,
            topic=fp_data["topic"] or req.tags,
            summary=fp_data["summary"],
            claims=fp_data["claims"], methods=fp_data["methods"],
            constraints=fp_data["constraints"], actions=fp_data["actions"],
            extractor_confidence=fp_data["extractor_confidence"],
            provenance=make_provenance(PROVENANCE_PROMPT_FASTPATH_DIRECT),
            lane=fp_data.get("lane", req.lane),
            status="fastpath",
        )
        s.add(cand); s.commit(); s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id, "fastpath": True}


def _create_candidate_with_extraction(req: AddMemoryReq) -> dict:
    """LLM 提取路径——原始逻辑"""
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    with db.get_session() as s:
        dedup_result = _apply_dedup(s, req.title, req.content, req.lane)
        if dedup_result is not None:
            return dedup_result
        existed = s.exec(select(RawDocument)
                         .where(RawDocument.content_hash == h)).first()
        if existed:
            doc = existed
        else:
            doc = RawDocument(
                id=new_id("doc"), source_type=req.source_type,
                source_id=req.url or h[:12], url=req.url,
                title=req.title, content=req.content, content_hash=h,
                meta=req.metadata,
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
            provenance=make_provenance(PROVENANCE_PROMPT_EXTRACT),
            lane=req.lane,
        )
        s.add(cand); s.commit(); s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id}


def add_memory_async(req: AddMemoryReq) -> dict:
    """异步批量写入（幂等）：COALESCE_ENABLED=false 时降级同步，不丢数据。

    COALESCE_ENABLED=true 时入队；若入队即触发冲刷，在此处持久化
    combined_content（缓冲数据绝不静默丢弃），失败则清除指纹允许重试。
    """
    buffer = get_coalesce_buffer()
    if not settings.COALESCE_ENABLED:
        result = add_memory(req)
        return {"status": "synced", "job_id": buffer.job_id("default", req.lane, req.content),
                **result}
    result = buffer.add_async("default", req.lane, req.content, req.title)
    if result.get("status") == "flushed":
        detail = result.get("detail") or {}
        req_copy = req.model_copy()
        req_copy.content = detail.get("combined_content", req.content)
        try:
            persisted = _create_candidate_with_extraction(req_copy)
        except Exception:
            buffer.forget_fingerprint(result["job_id"])
            # 该批其他消息已被 _flush 弹出：锁内恢复，避免静默丢失
            if detail.get("key"):
                buffer.requeue(detail["key"], detail.get("items", []))
            raise
        return {"status": "flushed", "job_id": result["job_id"], **persisted}
    return result


def set_decay_class(memory_id: str, decay_class: str) -> dict:
    """手动调整记忆衰减类；写 MemoryCheckpoint 以便回滚。"""
    if decay_class not in DECAY_CLASS_HALFLIFE:
        raise ValueError(f"invalid decay_class: {decay_class}")
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False, "reason": "memory missing"}
        before = {"decay_class": mem.decay_class}
        mem.decay_class = decay_class
        mem.updated_at = utcnow()
        _make_checkpoint(s, mem, before, "", trigger="decay_class")
        s.add(mem)
        s.commit()
        return {"ok": True, "memory_id": memory_id, "decay_class": decay_class}


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

def add_raw_memory(req: RawMemoryReq) -> dict:
    """原文直存（verbatim 记忆）：零 LLM、不走提取/闸门/演化，直接写 MemoryItem。

    幂等：内容 sha256 作 key，重复内容返回已有记忆（不重复索引）。
    """
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    lane = req.lane or settings.RAW_MEMORY_DEFAULT_LANE
    with db.get_session() as s:
        existing = s.exec(select(MemoryItem)
                          .where(MemoryItem.memory_type == "verbatim",
                                 MemoryItem.key == h,
                                 MemoryItem.status == "active")).first()
        if existing:
            return {"memory_id": existing.id, "dedup": True, "verbatim": True}
        emb = embed([req.content])[0]
        mem = MemoryItem(
            id=new_id("mem"),
            memory_type="verbatim",
            key=h,
            content=req.content,
            lane=lane,
            tier=MemoryTier.LONG_TERM,
            confidence=1.0,
            importance=0.5,
            tags=req.tags,
            decay_class="semantic",  # 原文直存衰减慢；procedural 永不衰减过强
        )
        s.add(mem)
        s.flush()
        index_memory_item(mem.id, emb, {"key": mem.key, "memory_type": mem.memory_type})
        sync_fts(s, mem.id, mem.content)
        s.commit()
        return {"memory_id": mem.id, "dedup": False, "verbatim": True}

def build_memories_page(
    session,
    lane: str = "",
    status: str = "",
    decay_class: str = "",
    memory_type: str = "",
    limit: int = 50,
    offset: int = 0,
    content_max: int = 160,
) -> dict:
    """档案浏览（VAULT，Ticket 06）：只读分页 + 过滤，updated_at 新→旧。

    纯函数：给定 session 直查，不打开会话（测试可直接传真实临时 session）。
    content 按 content_max 截断（超出加省略号），避免列表页拖全文。
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be in [1,100]")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if content_max < 0:
        raise ValueError("content_max must be >= 0")

    from sqlmodel import func

    conds = []
    if lane:
        conds.append(MemoryItem.lane == lane)
    if status:
        conds.append(MemoryItem.status == status)
    if decay_class:
        conds.append(MemoryItem.decay_class == decay_class)
    if memory_type:
        conds.append(MemoryItem.memory_type == memory_type)

    total = session.exec(
        select(func.count()).select_from(MemoryItem).where(*conds)
    ).one()
    rows = session.exec(
        select(MemoryItem)
        .where(*conds)
        .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.asc())
        .offset(offset)
        .limit(limit)
    ).all()

    def _row(m: MemoryItem) -> dict:
        content = m.content
        truncated = False
        if len(content) > content_max:
            content, truncated = content[:content_max], True
        return {
            "id": m.id,
            "memory_type": m.memory_type,
            "lane": m.lane,
            "status": m.status,
            "tier": m.tier,
            "decay_class": m.decay_class,
            "decay_score": m.decay_score,
            "use_count": m.use_count,
            "scene_id": m.scene_id,
            "created_at": m.created_at.isoformat(timespec="seconds") if m.created_at else None,
            "updated_at": m.updated_at.isoformat(timespec="seconds") if m.updated_at else None,
            "content": content + ("…" if truncated else ""),
        }

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "memories": [_row(m) for m in rows],
    }


def list_memories(
    lane: str = "",
    status: str = "",
    decay_class: str = "",
    memory_type: str = "",
    limit: int = 50,
    offset: int = 0,
    content_max: int = 160,
) -> dict:
    """打开默认会话执行档案浏览（只读）。"""
    with db.get_session() as s:
        return build_memories_page(
            s, lane=lane, status=status, decay_class=decay_class,
            memory_type=memory_type, limit=limit, offset=offset,
            content_max=content_max,
        )
