"""记忆写入与 CoreMemory service 层"""

import hashlib
from datetime import datetime

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.provenance import (
    PROVENANCE_PROMPT_EXTRACT,
    PROVENANCE_PROMPT_FASTPATH_DIRECT,
    PROVENANCE_PROMPT_VISION,
    make_provenance,
)
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.evolution.promoter import _make_checkpoint
from lantai.gate.dedup import find_similar
from lantai.ingestion.coalesce import get_coalesce_buffer
from lantai.llm.client import embed
from lantai.memory.decay_class import DECAY_CLASS_HALFLIFE
from lantai.models.enums import MemoryTier
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.models.tables import (
    CoreMemoryBlock,
    Evidence,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    RawDocument,
)
from lantai.parsing.extractor import extract_candidate
from lantai.parsing.fastpath import fastpath_check
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage import db
from lantai.storage.fts import sync_fts
from lantai.storage.vector_store import get_vector_store


def _principal_of(principal, user_id: str | None = None, tenant_id: str | None = None):
    """构造读侧 principal（票 `.scratch/readside-gaps/15`）。

    `add_memory` 一直接收 `user_id` / `tenant_id`，但只用来盖**新**候选的
    归属列，从不下传给去重——于是去重在**全库**范围找最近邻。这里把已有的
    两个参数补构造 principal：显式传入的优先，否则按 user_id 收敛
    （同 `_viewer_of` 口径，`None` → `"default"`）。

    **安全默认是收窄而非放行**：此前全库去重，本票之后按属主收敛。
    要看全库的调用方（admin / worker）显式传 admin principal。
    """
    if principal is not None:
        return principal
    from lantai.core.auth import Principal

    return Principal(
        user_id=user_id or "default",
        tenant_id=tenant_id,
        allowed_lanes=None,
    )


def _owns(mem, principal) -> bool:
    """行级归属判定（票 `.scratch/readside-gaps/15`）。

    `session.get` 按主键直读，**绕开一切 SQL 层 scope**（票 14 的 rejecter
    就是这么漏的）——所以向量层滤对了，这一下仍要单独判。

    NULL 属主（真实库 636/657 行）判「不可见」会让单人部署整体空转，
    故放行；admin 全权。口径同 `acl.ensure_can_delete`，但不抛异常——
    调用方要的是「跳过这条」而不是 403。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return True
    from lantai.core.acl import viewer_of

    return (mem.user_id or None) in (None, viewer_of(principal))


def _apply_dedup(
    s, content: str, fastpath: bool, principal=None
) -> tuple[str, MemoryItem | None, float]:
    """余弦预判（ADR-0019 结构判别第一相位）。

    返回 (action, target_or_None, sim)：
    - "merge"：余弦 ≥ merge 带（fastpath=0.90 / 提取路径预筛 0.95）→ 直合
    - "update"：fastpath 中带 → update 提案（有刹车）
    - "undecided"：提取路径中带 → 提取后交结构判别（relation.py）
    - "insert"：低相似 → 继续正常建候选

    归属（票 `.scratch/readside-gaps/15`）：此前 `search` 一个过滤都不带，
    返回**全库最近邻**——A 控制新记忆的正文即控制 embedding，反复试探即可
    命中 B 的记忆，随后 `_dedup_structural` 把 B 的正文送进外部 LLM、
    `_dedup_merge` 改 B 的 `importance`。现按 `principal` 收敛。
    """
    try:
        qv = embed([content])[0]
        from lantai.core.acl import vector_owner_filter

        filters = vector_owner_filter(principal)
        vec_results = get_vector_store().search(qv, top_k=1, filters=filters)
        if not isinstance(vec_results, list):
            return "insert", None, 0.0
        return find_similar(s, vec_results, fastpath=fastpath, principal=principal)
    except Exception as e:
        logger.warning("dedup prescreen failed (insert fallback): %s", e)
        return "insert", None, 0.0


def _dedup_merge(s, target: MemoryItem, sim: float, principal=None) -> dict | None:
    """merge 直合：仅 bump，不吞新文本（新文本已在更高相似带被排除）。

    归属（票 `.scratch/readside-gaps/15`）：这里改的是**别人的行**
    （`importance` +0.1、`last_used_at`），而上游已按主键直读——单独判一次，
    不放任「上游忘了传 principal」把缺口带到这里。
    """
    if not _owns(target, principal):
        logger.info("dedup merge: target %s 不归属当前主体，跳过（宁 miss 不脏写）", target.id)
        return None
    target.last_used_at = utcnow()
    target.importance = min(1.0, target.importance + 0.1)
    s.add(target)
    s.commit()
    return {"dedup_action": "merge", "target_memory_id": target.id, "similarity": round(sim, 4)}


def _create_update_proposal(
    s, target: MemoryItem, title: str, content: str, lane: str, sim: float, principal=None
) -> dict | None:
    """update 提案：待审，可批可拒（知识写入有刹车）。

    归属（票 `.scratch/readside-gaps/15`）：提案的 `target_memory_id` 钉在
    这条记忆上，将来 apply 时会改它的正文——不能指向别人的记忆。
    """
    if not _owns(target, principal):
        logger.info("dedup update: target %s 不归属当前主体，不建提案（宁 miss 不脏写）", target.id)
        return None
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
    return {
        "dedup_action": "update",
        "target_memory_id": target.id,
        "proposal_id": prop.id,
        "similarity": round(sim, 4),
    }


def _llm_judge(old: str, new: str) -> str:
    """中带 LLM 兜底：结构判别判不定的样本交 LLM 裁决。"""
    from lantai.llm.client import chat_json
    from lantai.llm.prompts import DEDUP_RELATION_SYS, DEDUP_RELATION_USER
    from lantai.services.prompt_service import get_prompt

    out = chat_json(
        get_prompt("DEDUP_RELATION_SYS", DEDUP_RELATION_SYS),
        get_prompt("DEDUP_RELATION_USER", DEDUP_RELATION_USER).format(old=old, new=new),
    )
    rel = (out or {}).get("relation")
    if rel not in ("merge", "update", "insert"):
        raise ValueError(f"bad relation: {rel}")
    return rel


def _dedup_structural(
    s, target_id: str, title: str, content: str, lane: str, sim: float, principal=None
) -> dict | None:
    """结构判别（ADR-0019 第二相位）：提取后对中带样本判类。

    返回 None = insert（继续建候选）；merge → 直合；update → 提案。
    规则吃不准（中带）交 LLM 兜底；LLM 缺席/失败 → insert（宁 miss 不脏写）。

    归属（票 `.scratch/readside-gaps/15`）：`target` 按主键直读后必须判归属
    才准送进 `classify_relation`——那个调用把 **B 的正文 + A 的正文一起
    发给外部 LLM**（`DEDUP_RELATION_*`），内容离开本机即无法撤回。
    别人的目标一律当 insert 处理（不改、不提案、不送 LLM）。
    """
    target = s.get(MemoryItem, target_id)
    if target is None or target.status != "active":
        return None
    if not _owns(target, principal):
        logger.info(
            "dedup structural: target %s 不归属当前主体，按 insert 处理（宁 miss 不脏写）",
            target_id,
        )
        return None
    if not settings.DEDUP_STRUCTURAL_ENABLED:
        # 关掉结构判别 → 保守走 update 提案（有刹车，不吞内容）
        return _create_update_proposal(s, target, title, content, lane, sim, principal)
    from lantai.gate.relation import classify_relation

    judge = _llm_judge if settings.DEDUP_STRUCTURAL_LLM_ENABLED else None
    rel = classify_relation(target.content, content, llm_judge=judge)
    if rel == "merge":
        return _dedup_merge(s, target, sim, principal)
    if rel == "update":
        return _create_update_proposal(s, target, title, content, lane, sim, principal)
    return None  # insert


def add_memory(
    req: AddMemoryReq,
    user_id: str = "default",
    *,
    tenant_id: str | None = None,
    principal=None,
) -> dict:
    """创建 RawDocument + MemoryCandidate。

    user_id / tenant_id：归属四元组（票 .scratch/ownership-gaps/03）。此前
    参数收了却不落列，`MemoryCandidate.user_id` 恒 NULL → 来源链继承到
    MemoryItem 也是 NULL → 属主过滤把**所有人**的检索结果过滤光。
    缺省 "default" 与 DEV MODE principal（auth.py:187）同值，内部调用方
    不传时仍落到可见归属而不是 NULL。

    principal（票 .scratch/readside-gaps/15）：去重范围的归属边界。
    显式传入优先；否则按 user_id 收敛（`_principal_of`）。REST 路由应传
    `Depends(get_current_user)` 的 ctx。
    """
    if (req.media_url or "").strip():
        from lantai.services.vision_service import build_vision_memory, vision_provenance_extra

        req = build_vision_memory(req)
        return _create_candidate_with_extraction(
            req,
            user_id=user_id,
            tenant_id=tenant_id,
            provenance_prompt=PROVENANCE_PROMPT_VISION,
            provenance_extra=vision_provenance_extra(req),
            principal=principal,
        )
    # Fastpath 白名单直写——缓冲前判断
    fp = fastpath_check(req.content)
    if fp:
        return _create_candidate_direct(
            req, fp, user_id=user_id, tenant_id=tenant_id, principal=principal
        )

    # Coalesce 开关——true 时走缓冲
    if settings.COALESCE_ENABLED:
        buffer = get_coalesce_buffer()
        result = buffer.add(
            user_id=user_id,
            lane=req.lane,
            content=req.content,
            title=req.title,
        )
        if result.get("buffered"):
            return {"buffered": True, "count": result.get("count", 0)}
        # 缓冲冲刷——批量提取；合并内容可能跨多个 session，
        # 出身宁可留空也不错误归属（宁 miss 不脏写）
        if result.get("flushed"):
            combined = result.get("combined_content", req.content)
            req_copy = req.model_copy()
            req_copy.content = combined
            req_copy.session_id = ""
            return _create_candidate_with_extraction(
                req_copy, user_id=user_id, tenant_id=tenant_id, principal=principal
            )

    # 默认同步路径
    return _create_candidate_with_extraction(
        req, user_id=user_id, tenant_id=tenant_id, principal=principal
    )


def _create_candidate_direct(
    req: AddMemoryReq,
    fp_data: dict,
    *,
    user_id: str = "default",
    tenant_id: str | None = None,
    principal=None,
) -> dict:
    """fastpath 命中——直接创建 RawDocument + MemoryCandidate，不走 LLM

    principal（票 readside-gaps/15）：下传去重链；未传则按 user_id 收敛
    （`_principal_of`）。
    """
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    p = _principal_of(principal, user_id, tenant_id)
    with db.get_session() as s:
        action, target, sim = _apply_dedup(s, req.content, fastpath=True, principal=p)
        if action == "merge" and target is not None:
            out = _dedup_merge(s, target, sim, p)
            if out is not None:
                return out
        elif action == "update" and target is not None:
            out = _create_update_proposal(s, target, req.title, req.content, req.lane, sim, p)
            if out is not None:
                return out
        doc = RawDocument(
            id=new_id("doc"),
            source_type=req.source_type,
            source_id=req.url or h[:12],
            url=req.url,
            title=req.title,
            content=req.content,
            content_hash=h,
            meta=req.metadata,
        )
        s.add(doc)
        s.commit()
        s.refresh(doc)

        cand = MemoryCandidate(
            id=new_id("cand"),
            # 归属四元组（票 .scratch/ownership-gaps/03）：不落列则来源链
            # 继承到 MemoryItem 也是 NULL，属主过滤把所有人的结果都滤光
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=(getattr(req, "session_id", "") or "").strip() or None,
            document_id=doc.id,
            topic=fp_data["topic"] or req.tags,
            summary=fp_data["summary"],
            claims=fp_data["claims"],
            methods=fp_data["methods"],
            constraints=fp_data["constraints"],
            actions=fp_data["actions"],
            extractor_confidence=fp_data["extractor_confidence"],
            provenance=make_provenance(PROVENANCE_PROMPT_FASTPATH_DIRECT),
            lane=fp_data.get("lane", req.lane),
            status="fastpath",
        )
        s.add(cand)
        s.commit()
        s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id, "fastpath": True}


def _create_candidate_with_extraction(
    req: AddMemoryReq,
    *,
    user_id: str = "default",
    tenant_id: str | None = None,
    provenance_prompt: str | None = None,
    provenance_extra: dict | None = None,
    principal=None,
) -> dict:
    """LLM 提取路径；provenance_prompt 覆盖默认 extract-v1（如 vision-caption）。

    principal（票 readside-gaps/15）：同 `_create_candidate_direct`。
    """
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    p = _principal_of(principal, user_id, tenant_id)
    with db.get_session() as s:
        action, target, sim = _apply_dedup(s, req.content, fastpath=False, principal=p)
        if action == "merge" and target is not None:
            out = _dedup_merge(s, target, sim, p)
            if out is not None:
                return out
        # undecided：提取后交结构判别；insert：正常建候选（仍提取）
        undecided_target_id = target.id if (action == "undecided" and target is not None) else None

    data = extract_candidate(req.title, req.content)

    with db.get_session() as s:
        if undecided_target_id is not None:
            structural = _dedup_structural(
                s, undecided_target_id, req.title, req.content, req.lane, sim, p
            )
            if structural is not None:
                return structural
        existed = s.exec(select(RawDocument).where(RawDocument.content_hash == h)).first()
        if existed:
            doc = existed
        else:
            doc = RawDocument(
                id=new_id("doc"),
                source_type=req.source_type,
                source_id=req.url or h[:12],
                url=req.url,
                title=req.title,
                content=req.content,
                content_hash=h,
                meta=req.metadata,
            )
            s.add(doc)
            s.commit()
            s.refresh(doc)

        # errsig 写侧登记（票据 03，与检索侧共用单一真源正则）：
        # 报错签名记入 provenance，供审计与优先级参考
        from lantai.retrieval.errsig import extract_error_signatures

        errsig_hits = list(extract_error_signatures(req.content))
        prov_extra = dict(provenance_extra or {})
        if errsig_hits:
            prov_extra["errsig"] = errsig_hits

        cand = MemoryCandidate(
            id=new_id("cand"),
            # 归属四元组（票 .scratch/ownership-gaps/03）：同 fastpath 路径口径
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=(getattr(req, "session_id", "") or "").strip() or None,
            document_id=doc.id,
            topic=data["topic"] or req.tags,
            summary=data["summary"],
            claims=data["claims"],
            methods=data["methods"],
            constraints=data["constraints"],
            actions=data["actions"],
            extractor_confidence=data["extractor_confidence"],
            provenance=make_provenance(
                provenance_prompt or PROVENANCE_PROMPT_EXTRACT,
                extra=prov_extra,
            ),
            lane=req.lane,
        )
        s.add(cand)
        s.commit()
        s.refresh(cand)
        return {"document_id": doc.id, "candidate_id": cand.id}


def add_memory_async(
    req: AddMemoryReq,
    user_id: str = "default",
    *,
    tenant_id: str | None = None,
    principal=None,
) -> dict:
    """异步批量写入（幂等）：COALESCE_ENABLED=false 时降级同步，不丢数据。

    COALESCE_ENABLED=true 时入队；若入队即触发冲刷，在此处持久化
    combined_content（缓冲数据绝不静默丢弃），失败则清除指纹允许重试。

    principal（票 readside-gaps/15）：同 `add_memory`，下传去重链。
    """
    buffer = get_coalesce_buffer()
    if not settings.COALESCE_ENABLED:
        result = add_memory(req, user_id=user_id, tenant_id=tenant_id, principal=principal)
        return {
            "status": "synced",
            "job_id": buffer.job_id(user_id, req.lane, req.content),
            **result,
        }
    result = buffer.add_async(user_id, req.lane, req.content, req.title)
    if result.get("status") == "flushed":
        detail = result.get("detail") or {}
        req_copy = req.model_copy()
        req_copy.content = detail.get("combined_content", req.content)
        try:
            persisted = _create_candidate_with_extraction(
                req_copy, user_id=user_id, tenant_id=tenant_id, principal=principal
            )
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


def get_core_memory(namespace: str = "default", principal=None) -> dict:
    """读取 CoreMemoryBlock 列表。

    归属过滤（票 .scratch/ownership-gaps/04）：此前本表一个归属列都没有，
    `/core-memory` 又不带身份，A 写的 policy 块任意登录用户一读就到。
    传入 principal 时非 admin 只见自己的块；admin 全见（同 acl.py 口径）。

    NULL 属主的老行（迁移 v25 之前写的）按 "default" 归属可见——与票 03
    「内部调用落到 DEV MODE 的 default」同一口径：NULL 是「未记录」的事实
    状态，不是「属于所有人」，否则要么永远看不见要么人人可读。
    principal=None 仅限内部调用（MCP / 脚本），同样按 "default" 收敛。
    """
    viewer = (
        (getattr(principal, "user_id", None) or "default") if principal is not None else "default"
    )
    is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
    with db.get_session() as s:
        stmt = select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
        if not is_admin:
            stmt = stmt.where(
                (CoreMemoryBlock.user_id == viewer) | (CoreMemoryBlock.user_id.is_(None))
            )
        blocks = s.exec(stmt.order_by(CoreMemoryBlock.block)).all()
        return {"blocks": [b.model_dump(mode="json") for b in blocks]}


def put_core_memory(block: str, content: str, namespace: str = "default", principal=None) -> dict:
    """创建或更新 CoreMemoryBlock（归属随 principal 落列，票 ownership-gaps/04）。

    非 admin 只能改自己的块：定位不到自己的行时**新建自己名下的块**，
    而不是覆写别人的（宁 miss 不越权）。
    """
    if block not in ("identity", "task", "policy"):
        raise ValueError("invalid block")
    if principal is None:
        tenant_id, user_id, agent_id = None, "default", None
    else:
        tenant_id = getattr(principal, "tenant_id", None)
        user_id = getattr(principal, "user_id", None) or "default"
        agent_id = getattr(principal, "agent_id", None)
    is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False

    with db.get_session() as s:
        stmt = select(CoreMemoryBlock).where(
            CoreMemoryBlock.block == block, CoreMemoryBlock.namespace == namespace
        )
        if not is_admin:
            # 只在自己的行（含未记录归属的老行）里找；找不到 → 自己名下新建
            stmt = stmt.where(
                (CoreMemoryBlock.user_id == user_id) | (CoreMemoryBlock.user_id.is_(None))
            )
        row = s.exec(stmt).first()
        if row:
            row.content = content
            row.version += 1
            # 归属列补写：老行可能是 NULL（迁移前写的），首次覆写即登记归属
            row.user_id = user_id
            row.tenant_id = tenant_id
            row.agent_id = agent_id
        else:
            row = CoreMemoryBlock(
                id=new_id("core"),
                block=block,
                namespace=namespace,
                content=content,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
            )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.model_dump(mode="json")


def build_verbatim_item(
    content: str,
    lane: str,
    tags: list | None = None,
    *,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> MemoryItem:
    """verbatim 直存项构造（纯函数）：sha256 幂等 key + 固定语义字段。

    add_raw_memory 与冷启动导入（services/import_service.py）共用，消除重复
    构造；created_at/updated_at 缺省取 utcnow，updated_at 缺省取 created_at
    （导入路径原样保留原始时间戳语义）。

    归属四元组（票 .scratch/ownership-gaps/03）：不传即如实 NULL——宁 miss
    不脏写，不猜归属；但调用方（尤其 HTTP 路由）必须把 principal 传下来，
    否则这条记忆在按属主过滤的检索里永久不可见。
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
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        created_at=created,
        updated_at=updated_at or created,
    )


def add_raw_memory(
    req: RawMemoryReq,
    *,
    user_id: str = "default",
    tenant_id: str | None = None,
    agent_id: str | None = None,
    principal=None,
) -> dict:
    """原文直存（verbatim 记忆）：零 LLM、不走提取/闸门/演化，直接写 MemoryItem。

    幂等：内容 sha256 作 key，重复内容返回已有记忆（不重复索引）。

    归属四元组（票 .scratch/ownership-gaps/03）：此前签名里没有这些参数，
    verbatim 是条数最多的一类（本机真实库 391/635 条），属主恒 NULL 让
    `fts.py:160` 的 `AND m.user_id = ?` 把它们全部滤掉。

    去重补归属（票 `.scratch/readside-gaps/20`）：去重查询此前
    `memory_type + key + status` 三个条件、**不带任何归属过滤**——A 提交
    一段与 B 的 verbatim 内容 sha256 相同的文本（读同一篇公开文档即可构造），
    返回的就是 **B 的** `memory_id`。调用方 `obsidian_service.sync_obsidian_note`
    拿这个 id 去 `s.get(MemoryItem, note_id)`，于是 A 的下一次 sync 会把
    **A 的双链实体从 B 的记忆行连出去**（`links` 边方向是「笔记 → 实体」）。
    所以去重按 `user_id == viewer OR IS NULL` 收窄。

    **NULL 属主判「重复」不判「新建」**：这是本函数唯一需要拍价值观的地方，
    实测支撑——真实库 391 条 verbatim **全部** `user_id` 为 NULL。verbatim 是
    **内容寻址**（sha256 即 id 语义），同一段文本就是同一条记忆，属主只是
    标注、不是身份判据。判「新建」会让每一条既有无主 verbatim 都无法去重，
    同一内容每 sync 一次就多存一份——把内容寻址退化成多份存储。
    """
    h = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    lane = req.lane or settings.RAW_MEMORY_DEFAULT_LANE
    with db.get_session() as s:
        q = select(MemoryItem).where(
            MemoryItem.memory_type == "verbatim",
            MemoryItem.key == h,
            MemoryItem.status == "active",
        )
        # 去重归属（票 20）：admin / principal=None → 不过滤（worker/MCP 不能空转）；
        # 否则收窄到 `user_id == viewer OR IS NULL`（NULL 老行判重复，见 docstring）。
        if principal is not None and not bool(getattr(principal, "is_admin", False)):
            from lantai.core.acl import viewer_of

            q = q.where(
                (MemoryItem.user_id == viewer_of(principal)) | (MemoryItem.user_id.is_(None))
            )
        existing = s.exec(q).first()
        if existing:
            return {"memory_id": existing.id, "dedup": True, "verbatim": True}
        emb = embed([req.content])[0]
        mem = build_verbatim_item(
            req.content,
            lane,
            tags=req.tags,
            user_id=user_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        # 来源链（v022 票据 01）：直存也带 session 出身；空则 NULL
        if (getattr(req, "session_id", "") or "").strip():
            mem.session_id = req.session_id.strip()
        s.add(mem)
        s.flush()
        index_memory_item(
            mem.id,
            emb,
            {
                "key": mem.key,
                "memory_type": mem.memory_type,
                "lane": getattr(mem, "lane", "general") or "general",
                "domain": getattr(mem, "domain", "user") or "user",
                "tenant_id": getattr(mem, "tenant_id", "") or "",
                "user_id": getattr(mem, "user_id", "") or "",
                "session_id": getattr(mem, "session_id", "") or "",
                "agent_id": getattr(mem, "agent_id", "") or "",
            },
        )
        sync_fts(s, mem.id, mem.content)
        s.add(
            Evidence(
                id=new_id("ev"),
                tenant_id=mem.tenant_id,
                user_id=mem.user_id,
                agent_id=mem.agent_id,
                session_id=mem.session_id,
                evidence_type="verbatim_ingest",
                source_memory_id=mem.id,
                content=mem.content,
                reliability=1.0,
                independence=1.0,
                provenance={"source": "raw_add", "lane": lane},
                created_at=utcnow(),
            )
        )
        s.commit()
        return {"memory_id": mem.id, "dedup": False, "verbatim": True}


def build_memories_page(
    session,
    lane: str = "",
    principal=None,
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

    # 归属·无身份（票 `.scratch/mcp-identity-gaps/02` 续）：`principal=None`
    # 时**此前一个归属条件都不加**——整个块挂在下面的 `if principal:` 里，
    # 同文件 :752 那段精心写的 `viewer_of` 收敛 + `OR IS NULL` 整段不可达。
    # MCP `mem_recent` 的宿主不透传 `user_id` 时正是 None
    # （`mcp.py:132` 的 `_principal_from_params` 返回 None），于是一次调用
    # 拿走全库记忆正文。实测（`.scratch/mcp-identity-gaps/
    # probe_02c_three_way.py`）：None → 4 条（含 B 的私有记忆正文），
    # user-A → 2 条，user-B → 2 条。
    #
    # 收敛到 `"default"` 而不是一律拒，也不是保持全表：
    # · 保持全表 = 上面那个洞，不修；
    # · 一律拒 = MCP 客户端（普遍不传 user_id）的 `mem_recent` 全部空转；
    # · 收敛到 default = 与同文件 :503/:532/:644 三处、`cognitive_context`、
    #   `candidates_pending`、`build_overview` 同一口径（那些实测都是
    #   None → default），不新造第四种。
    # 真实库唯一非空属主就是 `default`，单人部署收敛后照见自己的全部历史。
    #
    # **只改 None 这一种形态**，显式 principal 与 admin 逐字不动：
    # admin 走 HTTP 时是 `user_id="api_key", role="admin"`
    # （`auth.py:168`），本来就落进下面的 user 分支被收窄；admin 带
    # `user_id=None` 时则靠下面的 user 判空**意外**绕过收窄。两种形态行为
    # 不同是既存事实，统一它属于另一处改动（已另开票），本票不夹带——
    # 按 01b 教训分开提交、分开验证。
    if principal is None:
        from lantai.core.acl import viewer_of

        conds.append((MemoryItem.user_id == viewer_of(None)) | (MemoryItem.user_id.is_(None)))

    if principal:
        # 归属·admin（票 `.scratch/mcp-identity-gaps/08`）：admin 一律不加
        # user 归属过滤，与同文件 :73（`_kaogong_scope`）/ :498
        # （`get_core_memory`）/ :523（`put_core_memory`）/ :640
        # （`find_duplicate_verbatim`）四处形状逐字一致——那四处都显式
        # `is_admin` → 不加 scope，只有本站靠在 `user_id` 上判空"意外"放过了
        # `user_id=None` 的 admin，`user_id="api_key"` 的 admin（`auth.py:168`
        # HTTP 环境变量 API key 的真形态）则被收窄到 `user_id=='api_key'
        # OR IS NULL`——真实库没有这个属主的行，**档案页基本空白**，
        # 排查"这条记忆去哪了"会得到错误结论。
        # 实测（`.scratch/mcp-identity-gaps/probe_08_admin_two_forms.py`，修前）：
        #   user_id='api_key' → total=1（只剩 NULL 老行）
        #   user_id=None      → total=3
        # **只统一 user_id 这一处**（两种形态差异的唯一来源）：tenant /
        # session / agent / allowed_lanes 是调用方显式传的收窄条件，不属
        # 身份差异；顺带放开会把「admin 全表」扩大成「admin 无条件」，
        # 那是另一个决定，不夹带在本票。
        is_admin = bool(getattr(principal, "is_admin", False))
        if getattr(principal, "tenant_id", None):
            conds.append(MemoryItem.tenant_id == principal.tenant_id)
        if getattr(principal, "user_id", None) and not is_admin:
            # 归属（票 .scratch/mcp-identity-gaps/03）：补 `OR IS NULL`，
            # 与同文件 :503/:532/:644 三处口径逐字一致。此前这里是裸
            # `== principal.user_id`——NULL 属主老行（v022 之前写入没有
            # 属主概念，真实库 650 行里 629 行）对 DEV MODE 与任何显式
            # 属主用户**全部不可见**，单用户部署的 VAULT 档案页基本空白。
            # 实测：修前 DEV MODE 只见 3/650，修后 632/650。
            #
            # `OR IS NULL` 不是"放开隔离"：B 的行仍然不可见（老行人人可读，
            # 他人新行不可读），测试 `test_explicit_owner_sees_null_rows_but_not_others`
            # 专门锁这条，防"把条件整个删掉"的变异。
            #
            # 用 `acl.viewer_of` 收敛而非直接取 `principal.user_id`：前者对
            # 空 user_id 回落到 "default"，与 DEV MODE 同值，不新造默认属主。
            from lantai.core.acl import viewer_of

            viewer = viewer_of(principal)
            conds.append((MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None)))
        if getattr(principal, "session_id", None):
            conds.append(MemoryItem.session_id == principal.session_id)
        if getattr(principal, "agent_id", None):
            conds.append(MemoryItem.agent_id == principal.agent_id)
        if getattr(principal, "allowed_lanes", None) is not None:
            from sqlmodel import col

            conds.append(col(MemoryItem.lane).in_(principal.allowed_lanes))

    total = session.exec(select(func.count()).select_from(MemoryItem).where(*conds)).one()
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
            # 归属标识（票 .scratch/ownership-gaps/01）：让调用方能自查这条
            # 是不是自己的——此前列表页看不出归属，越权与否只能靠猜。
            "user_id": m.user_id,
            "tenant_id": m.tenant_id,
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
    principal=None,
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
            s,
            lane=lane,
            status=status,
            decay_class=decay_class,
            memory_type=memory_type,
            limit=limit,
            offset=offset,
            content_max=content_max,
            principal=principal,
        )
