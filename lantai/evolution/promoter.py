from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.provenance import PROVENANCE_PROMPT_DIALOGUE_IMPORT
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.llm.client import embed
from lantai.memory.decay_class import infer_decay_class
from lantai.models.enums import MemoryTier, ProposalStatus
from lantai.models.tables import (
    Evidence,
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryEdge,
    MemoryItem,
    MemoryProposal,
)
from lantai.retrieval.hybrid import delete_memory_item, index_memory_item
from lantai.storage import db
from lantai.storage.fts import sync_fts


def _make_checkpoint(session, mem: MemoryItem, before: dict, proposal_id: str, trigger: str):
    session.add(
        MemoryCheckpoint(
            id=new_id("ckpt"),
            memory_id=mem.id,
            version=mem.version,
            before=before,
            after=mem.model_dump(mode="json"),
            proposal_id=proposal_id,
            trigger=trigger,
        )
    )


def apply_proposal(proposal_id: str) -> dict:
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        # 可应用状态：PENDING（evolve 自动路径）或 APPROVED（人工审批/补跑路径）
        if not prop or prop.status not in (ProposalStatus.PENDING, ProposalStatus.APPROVED):
            return {"ok": False, "reason": "not applicable"}

        patch = prop.proposed_patch
        mem_type = patch.get("memory_type", "semantic")
        key = patch.get("key")
        content = patch.get("content", "")
        lane = patch.get("lane", settings.DEFAULT_LANE)

        existing = None
        if prop.target_memory_id:
            existing = s.get(MemoryItem, prop.target_memory_id)
            if existing and existing.status != "active":
                existing = None
        if existing is None and key:
            existing = s.exec(
                select(MemoryItem).where(MemoryItem.key == key, MemoryItem.status == "active")
            ).first()

        emb = embed([content])[0] if content else []
        # 分支附加返回（ADR-0050：evidence 三分缺口记入 apply 返回与日志）；其余分支为空
        apply_extra: dict = {}

        # 反思模块（spec: docs/plans/reflection-module-spec.md）：deprecate / merge 分支。
        # add/update 语义保持不动（门面铁律）；deprecate/merge 都走 checkpoint + supersedes 边。
        # 沉潜过审（ADR-0050）：consolidation 分支见下方显式 elif（必须先于 add 捕获分支）。
        if prop.proposal_type == "deprecate" and existing:
            before = existing.model_dump(mode="json")
            existing.valid_to = utcnow()
            existing.status = "archived"
            existing.version += 1
            existing.updated_at = utcnow()
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="reflect")
            if prop.evidence_ids:
                dup = s.exec(
                    select(MemoryEdge).where(
                        MemoryEdge.relation == "supersedes",
                        MemoryEdge.source_memory_id == prop.evidence_ids[0],
                        MemoryEdge.target_memory_id == existing.id,
                    )
                ).first()
                if dup is None:
                    s.add(
                        MemoryEdge(
                            id=new_id("edge"),
                            source_memory_id=prop.evidence_ids[0],
                            target_memory_id=existing.id,
                            relation="supersedes",
                            confidence=prop.confidence,
                        )
                    )
            sync_fts(s, existing.id, None)
            delete_memory_item(existing.id)
        elif prop.proposal_type == "merge" and existing:
            before = existing.model_dump(mode="json")
            existing.content = content or existing.content
            existing.version += 1
            existing.updated_at = utcnow()
            existing.source_ids = list(set(existing.source_ids + prop.evidence_ids))
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="reflect")
            emb2 = embed([existing.content])[0]
            index_memory_item(
                existing.id,
                emb2,
                {
                    "key": existing.key,
                    "memory_type": existing.memory_type,
                    "lane": getattr(existing, "lane", "general") or "general",
                    "domain": getattr(existing, "domain", "user") or "user",
                    "tenant_id": getattr(existing, "tenant_id", "") or "",
                    "user_id": getattr(existing, "user_id", "") or "",
                    "session_id": getattr(existing, "session_id", "") or "",
                    "agent_id": getattr(existing, "agent_id", "") or "",
                },
            )
            sync_fts(s, existing.id, existing.content)
            for eid in prop.evidence_ids:
                if eid == existing.id:
                    continue
                src = s.get(MemoryItem, eid)
                if not src or src.status != "active":
                    continue
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=existing.id,
                        target_memory_id=src.id,
                        relation="supersedes",
                        confidence=prop.confidence,
                    )
                )
                src_before = src.model_dump(mode="json")
                src.status = "archived"
                src.version += 1
                src.updated_at = utcnow()
                s.add(src)
                _make_checkpoint(s, src, src_before, prop.id, trigger="reflect")
                sync_fts(s, src.id, None)
                delete_memory_item(src.id)
        elif prop.proposal_type == "consolidation":
            # 沉潜过审（ADR-0050/票 07）：巩固提案 apply——一个事务内主记忆落库＋碎片折叠
            # ＋supersedes 边＋逐条真实 id checkpoint＋索引同步。本分支必须显式插在 add
            # 捕获分支之前：add 分支会吞掉任何未知 proposal_type（落主记忆但不折叠碎片、
            # checkpoint 变 gate、多出 supports 边与 Evidence）。
            evidence_ids = list(dict.fromkeys(prop.evidence_ids or []))
            # stale 硬门（ADR-0050 决策 3）：任一 evidence 已 consolidated 即拒绝——
            # consolidated 只能由一次折叠产生，出现即意味存在 off 期直写产物或先行
            # apply 的重叠提案，继续执行将产出双主记忆重复召回（宁 miss 不脏写）。
            stale_hits = []
            for eid in evidence_ids:
                src = s.get(MemoryItem, eid)
                if src is not None and src.status == "consolidated":
                    stale_hits.append(eid)
            if stale_hits:
                logger.warning(
                    "巩固提案 %s apply 拒绝：evidence 已 consolidated（stale 硬门）%s",
                    prop.id,
                    stale_hits,
                )
                # 落终态 REJECTED（ADR-0050 决策 3「拒绝该提案」的终态语义）：apply 若仅
                # 早退而不改状态，decide_proposal 先置的 APPROVED 将永留，evolve_worker
                # .run_pending_proposals（专捞 APPROVED）每轮重试每次失败＝livelock。
                # 拒绝理由与命中的 evidence 一并留痕（宁 miss 不脏写、不静默）。
                prop.status = ProposalStatus.REJECTED
                prop.decision_reason = (
                    f"stale: evidence already consolidated {stale_hits}"
                    "（ADR-0050 决策 3 硬门：consolidated 只能由一次折叠产生，"
                    "继续执行将产出双主记忆重复召回）"
                )
                # decided_at（ADR-0053）：stale 硬门即系统裁决时刻，与人工 reject 同口径落值
                prop.decided_at = utcnow()
                s.add(prop)
                s.commit()
                return {
                    "ok": False,
                    "reason": "stale: evidence already consolidated",
                    "detail": {"consolidated_evidence_ids": stale_hits},
                }
            patch_full = prop.proposed_patch
            # 主记忆构造取 proposed_patch 不重推断（生成侧 _master_patch 平移现状构造：
            # decay_class 恒 semantic、decay_score=1.0、不设四元组——行为零漂移）
            master = MemoryItem(
                id=new_id("mem"),
                content=patch_full.get("content", ""),
                domain=patch_full.get("domain", "user"),
                lane=patch_full.get("lane", settings.DEFAULT_LANE),
                source_ids=list(patch_full.get("source_ids") or evidence_ids),
                confidence=float(patch_full.get("confidence", prop.confidence)),
                importance=float(patch_full.get("importance", 0.5)),
                decay_score=1.0,
                decay_class=patch_full.get("decay_class", "semantic"),
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(master)
            s.flush()
            # 主记忆为新建实体：before={} 空 dict（仿 add 分支先例），checkpoint 带真实 id
            _make_checkpoint(s, master, {}, prop.id, trigger="consolidation")
            index_memory_item(
                master.id,
                emb,
                {
                    "key": master.key,
                    "memory_type": master.memory_type,
                    "lane": getattr(master, "lane", "general") or "general",
                    "domain": getattr(master, "domain", "user") or "user",
                    "tenant_id": getattr(master, "tenant_id", "") or "",
                    "user_id": getattr(master, "user_id", "") or "",
                    "session_id": getattr(master, "session_id", "") or "",
                    "agent_id": getattr(master, "agent_id", "") or "",
                },
            )
            sync_fts(s, master.id, master.content)
            # 碎片现状三分（pending 期碎片并非零变更：遗忘/同周期 prune/笔削/晚更正均可
            # 变更或移除；TrustMem 不重跑，提纯基线过时风险由裁决者凭 evidence 现状判断）
            folded = edge_only = missing = 0
            for eid in evidence_ids:
                src = s.get(MemoryItem, eid)
                if src is None:
                    # 行已删除：不建边（边指向幽灵碎片即脏写），缺口留日志与返回
                    missing += 1
                    continue
                # supersedes 边（主记忆→碎片，方向同 merge 分支）；巩固不是新证据入树，
                # 不复用 add 分支的 supports 边与 Evidence
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=master.id,
                        target_memory_id=src.id,
                        relation="supersedes",
                        confidence=prop.confidence,
                    )
                )
                if src.status == "active":
                    # 仍 active：折叠＋checkpoint（before＝apply 时刻实际现状，
                    # 非「巩固前现场」——生成时刻基线由提案 proposed_patch/provenance 承载）
                    src_before = src.model_dump(mode="json")
                    src.status = "consolidated"
                    src.version += 1
                    src.updated_at = utcnow()
                    s.add(src)
                    _make_checkpoint(s, src, src_before, prop.id, trigger="consolidation")
                    sync_fts(s, src.id, None)
                    delete_memory_item(src.id)
                    folded += 1
                else:
                    # 已 archived（或 retracted 等其他非 active 态）：仅补 supersedes 边
                    # 血缘补记，不改状态、无 checkpoint
                    edge_only += 1
            if missing or edge_only:
                logger.info(
                    "巩固提案 %s apply：主记忆 %s 落库，折叠 %d 条碎片，"
                    "%d 条已非 active 仅补血缘边，%d 条已删除不建边",
                    prop.id,
                    master.id,
                    folded,
                    edge_only,
                    missing,
                )
            else:
                logger.info(
                    "巩固提案 %s apply：主记忆 %s 落库，折叠 %d 条碎片",
                    prop.id,
                    master.id,
                    folded,
                )
            apply_extra = {
                "master_id": master.id,
                "folded": folded,
                "edge_only": edge_only,
                "evidence_missing": missing,
            }
        elif prop.proposal_type == "add" or not existing:
            source_ids = list(set(prop.evidence_ids))
            tier = (
                MemoryTier.LONG_TERM
                if len(source_ids) >= settings.PROMOTE_SEMANTIC_MIN_SOURCES
                else MemoryTier.WORKING
            )
            structure = patch.get("structure") or {}
            mem_kwargs = dict(
                id=new_id("mem"),
                memory_type=mem_type,
                key=key or content[:60],
                content=content,
                tier=tier,
                source_ids=source_ids,
                confidence=prop.confidence,
                importance=0.5,
                lane=lane,
                structure=structure,
                provenance=prop.provenance or {},
                # 不信任 proposal 携带的 metadata 覆盖 decay_class（不可信来源）；
                # 仅按内容关键词推断，显式调级走 service 层 set_decay_class（带 checkpoint）
                decay_class=infer_decay_class(key or "", content),
            )
            # 冷启动导入（dialogue-session-import）：候选原始时间戳继承到
            # MemoryItem.created_at——历史会话时间线不压平到导入时刻
            src_cand = s.get(MemoryCandidate, prop.candidate_id) if prop.candidate_id else None
            if prop.provenance.get("prompt") == PROVENANCE_PROMPT_DIALOGUE_IMPORT:
                if src_cand and src_cand.created_at:
                    mem_kwargs["created_at"] = src_cand.created_at
            # 来源链继承（v022 票据 01）：出身由写入方显式声明，随
            # candidate → proposal → MemoryItem 落值；空则如实 NULL
            if src_cand is not None:
                if src_cand.session_id:
                    mem_kwargs["session_id"] = src_cand.session_id
                if src_cand.user_id:
                    mem_kwargs["user_id"] = src_cand.user_id
            # 更漏（ADR-0048/票 08）：提案携带显式事件时间 → I1 校验后落列；
            # 违例拒写留痕（宁 miss 不脏写，不静默修正）
            et = patch.get("event_time")
            etp = patch.get("event_time_precision", "")
            if et is not None or etp:
                from lantai.core.time_precision import validate_event_time_pair

                if isinstance(et, str):
                    from lantai.core.time import parse_iso_utc

                    try:
                        et = parse_iso_utc(et)
                    except ValueError:
                        return {
                            "ok": False,
                            "reason": "invalid event_time pair (I1)",
                            "detail": {"event_time": et, "event_time_precision": etp},
                        }
                if not validate_event_time_pair(et, etp):
                    return {
                        "ok": False,
                        "reason": "invalid event_time pair (I1)",
                        "detail": {"event_time": str(et), "event_time_precision": etp},
                    }
                mem_kwargs["event_time"] = et
                mem_kwargs["event_time_precision"] = etp
            mem = MemoryItem(**mem_kwargs)
            # Skill 资产化：提案携带步骤结构 → 视为技能（procedural 永不衰减）
            if structure.get("steps"):
                mem.decay_class = "procedural"
            s.add(mem)
            s.flush()
            _make_checkpoint(s, mem, {}, prop.id, trigger="gate")
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
            # 自动创建关系边——用外层 session 同事务写入（独立 session 会触发 SQLite 自锁）
            for evidence_id in prop.evidence_ids:
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=evidence_id,
                        target_memory_id=mem.id,
                        relation="supports",
                        confidence=prop.confidence,
                    )
                )
            # 闭环：新写入 MemoryItem 时为其创建对应的根证据 Evidence
            s.add(
                Evidence(
                    id=new_id("ev"),
                    tenant_id=mem.tenant_id,
                    user_id=mem.user_id,
                    agent_id=mem.agent_id,
                    session_id=mem.session_id,
                    evidence_type="proposal_applied",
                    source_memory_id=mem.id,
                    content=mem.content,
                    reliability=max(0.6, prop.confidence),
                    independence=1.0,
                    provenance=prop.provenance or {},
                    created_at=utcnow(),
                )
            )
        else:
            before = existing.model_dump(mode="json")
            existing.content = content
            existing.version += 1
            existing.updated_at = utcnow()
            existing.source_ids = list(set(existing.source_ids + prop.evidence_ids))
            existing.confidence = max(existing.confidence, prop.confidence)
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="evolve")
            index_memory_item(
                existing.id,
                emb,
                {
                    "key": existing.key,
                    "memory_type": existing.memory_type,
                    "lane": getattr(existing, "lane", "general") or "general",
                    "domain": getattr(existing, "domain", "user") or "user",
                    "tenant_id": getattr(existing, "tenant_id", "") or "",
                    "user_id": getattr(existing, "user_id", "") or "",
                    "session_id": getattr(existing, "session_id", "") or "",
                    "agent_id": getattr(existing, "agent_id", "") or "",
                },
            )
            sync_fts(s, existing.id, existing.content)

        prop.status = ProposalStatus.APPLIED
        prop.applied_at = utcnow()
        s.add(prop)
        s.commit()
        return {"ok": True, "proposal_id": prop.id, **apply_extra}


def rollback(memory_id: str) -> dict:
    with db.get_session() as s:
        ckpts = s.exec(
            select(MemoryCheckpoint)
            .where(MemoryCheckpoint.memory_id == memory_id)
            .order_by(MemoryCheckpoint.version.desc())
        ).all()
        if len(ckpts) < 2:
            return {"ok": False, "reason": "no previous version"}
        prev = ckpts[1]
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False, "reason": "memory missing"}
        before = mem.model_dump(mode="json")
        for k, v in prev.after.items():
            if hasattr(mem, k) and k != "id":
                setattr(mem, k, v)
        mem.version += 1
        mem.updated_at = utcnow()
        s.add(mem)
        _make_checkpoint(s, mem, before, proposal_id="", trigger="rollback")
        sync_fts(s, mem.id, mem.content)
        s.commit()
        return {"ok": True}


def delete_memory(memory_id: str) -> dict:
    """删除记忆（从 SQLite + 向量存储）"""
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if mem:
            s.delete(mem)
            sync_fts(s, memory_id, None)
            s.commit()
    delete_memory_item(memory_id)
    return {"ok": True}
