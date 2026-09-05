"""记忆写入与 CoreMemory service 层"""
import hashlib
from lantai.core.logger import logger
from datetime import datetime

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import RawDocument, MemoryCandidate, CoreMemoryBlock, MemoryItem, MemoryProposal
from lantai.models.enums import MemoryTier
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.llm.client import embed
from lantai.core.provenance import (
    PROVENANCE_PROMPT_EXTRACT, PROVENANCE_PROMPT_FASTPATH_DIRECT,
    PROVENANCE_PROMPT_VISION, make_provenance)
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


def _apply_dedup(s, content: str, fastpath: bool) -> tuple[str, MemoryItem | None, float]:
    """余弦预判（ADR-0019 结构判别第一相位）。

    返回 (action, target_or_None, sim)：
    - "merge"：余弦 ≥ merge 带（fastpath=0.90 / 提取路径预筛 0.95）→ 直合
    - "update"：fastpath 中带 → update 提案（有刹车）
    - "undecided"：提取路径中带 → 提取后交结构判别（relation.py）
    - "insert"：低相似 → 继续正常建候选
    """
    try:
        qv = embed([content])[0]
        vec_results = vector_store.search(qv, top_k=1)
        if not isinstance(vec_results, list):
            return "insert", None, 0.0
        return find_similar(s, vec_results, fastpath=fastpath)
    except Exception as e:
        logger.warning("dedup prescreen failed (insert fallback): %s", e)
        return "insert", None, 0.0


def _dedup_merge(s, target: MemoryItem, sim: float) -> dict:
    """merge 直合：仅 bump，不吞新文本（新文本已在更高相似带被排除）。"""
    target.last_used_at = utcnow()
    target.importance = min(1.0, target.importance + 0.1)
    s.add(target)
    s.commit()
    return {"dedup_action": "merge", "target_memory_id": target.id, "similarity": round(sim, 4)}


def _create_update_proposal(s, target: MemoryItem, title: str, content: str,
                            lane: str, sim: float) -> dict:
    """update 提案：待审，可批可拒（知识写入有刹车）。"""
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


def _llm_judge(old: str, new: str) -> str:
    """中带 LLM 兜底：结构判别判不定的样本交 LLM 裁决。"""
    from lantai.llm.client import chat_json
    from lantai.llm.prompts import DEDUP_RELATION_SYS, DEDUP_RELATION_USER
    out = chat_json(DEDUP_RELATION_SYS, DEDUP_RELATION_USER.format(old=old, new=new))
    rel = (out or {}).get("relation")
    if rel not in ("merge", "update", "insert"):
        raise ValueError(f"bad relation: {rel}")
    return rel


def _dedup_structural(s, target_id: str, title: str, content: str,
                      lane: str, sim: float) -> dict | None:
    """结构判别（ADR-0019 第二相位）：提取后对中带样本判类。

    返回 None = insert（继续建候选）；merge → 直合；update → 提案。
    规则吃不准（中带）交 LLM 兜底；LLM 缺席/失败 → insert（宁 miss 不脏写）。
    """
    target = s.get(MemoryItem, target_id)
    if target is None or target.status != "active":
        return None
    if not settings.DEDUP_STRUCTURAL_ENABLED:
        # 关掉结构判别 → 保守走 update 提案（有刹车，不吞内容）
        return _create_update_proposal(s, target, title, content, lane, sim)
    from lantai.gate.relation import classify_relation
    judge = _llm_judge if settings.DEDUP_STRUCTURAL_LLM_ENABLED else None
    rel = classify_relation(target.content, content, llm_judge=judge)
    if rel == "merge":
        return _dedup_merge(s, target, sim)
    if rel == "update":
        return _create_update_proposal(s, target, title, content, lane, sim)
    return None  # insert


def add_memory(req: AddMemoryReq) -> dict:
    """创建 RawDocument + MemoryCandidate。

    当 COALESCE_ENABLED=true 时走缓冲路径。
    fastpath 命中时直接写入（绕过 LLM 提取）。
    media_url（目识 vision）时：caption 生成后走提取路径，溯源记
    vision-caption + media_url；失败在 vision_service 抛 ValueError（422）。
    """
    if (req.media_url or "").strip():
        from lantai.services.vision_service import (
            build_vision_memory, vision_provenance_extra)
        req = build_vision_memory(req)
        return _create_candidate_with_extraction(
            req,
            provenance_prompt=PROVENANCE_PROMPT_VISION,
            provenance_extra=vision_provenance_extra(req),
        )
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
        action, target, sim = _apply_dedup(s, req.content, fastpath=True)
        if action == "merge" and target is not None:
            return _dedup_merge(s, target, sim)
        if action == "update" and target is not None:
            return _create_update_proposal(s, target, req.title, req.content, req.lane, sim)
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


def _create_candidate_with_extraction(
    req: AddMemoryReq, provenance_prompt: str | None = None,
    provenance_extra: dict | None = None,
) -> dict:
    """LLM 提取路径；provenance_prompt 覆盖默认 extract-v1（如 vision-caption）。"""
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    with db.get_session() as s:
        action, target, sim = _apply_dedup(s, req.content, fastpath=False)
        if action == "merge" and target is not None:
            return _dedup_merge(s, target, sim)
        # undecided：提取后交结构判别；insert：正常建候选（仍提取）
        undecided_target_id = target.id if (action == "undecided" and target is not None) else None

    data = extract_candidate(req.title, req.content)

    with db.get_session() as s:
        if undecided_target_id is not None:
            structural = _dedup_structural(
                s, undecided_target_id, req.title, req.content, req.lane, sim)
            if structural is not None:
                return structural
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

        cand = MemoryCandidate(
            id=new_id("cand"), document_id=doc.id,
            topic=data["topic"] or req.tags,
            summary=data["summary"],
            claims=data["claims"], methods=data["methods"],
            constraints=data["constraints"], actions=data["actions"],
            extractor_confidence=data["extractor_confidence"],
            provenance=make_provenance(
                provenance_prompt or PROVENANCE_PROMPT_EXTRACT,
                extra=provenance_extra,
            ),
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

def build_verbatim_item(content: str, lane: str, tags: list | None = None, *,
                        created_at: datetime | None = None,
                        updated_at: datetime | None = None) -> MemoryItem:
    """verbatim 直存项构造（纯函数）：sha256 幂等 key + 固定语义字段。

    add_raw_memory 与冷启动导入（services/import_service.py）共用，消除重复
    构造；created_at/updated_at 缺省取 utcnow，updated_at 缺省取 created_at
    （导入路径原样保留原始时间戳语义）。
    """
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    created = created_at or utcnow()
    return MemoryItem(
        id=new_id("mem"),
        memory_type="verbatim",
        key=h,
        content=content,
        lane=lane,
        tier=MemoryTier.LONG_TERM,
        confidence=1.0,
        importance=0.5,
        tags=tags or [],
        decay_class="semantic",  # 原文直存衰减慢；procedural 永不衰减过强
        created_at=created,
        updated_at=updated_at or created,
    )


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
        mem = build_verbatim_item(req.content, lane, tags=req.tags)
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
