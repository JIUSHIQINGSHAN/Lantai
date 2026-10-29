"""探颐（ADR-0037）：记忆主动探针与自然交互消歧服务。

功能：
1. detect_memory_probes: 检索命中未决冲突时，生成主动澄清探针；
2. format_probing_context: 格式化 Prompt 注入上下文；
3. resolve_probe_response: 根据用户次轮自然语言答复自动闭环消解冲突。
"""

import re

import jieba
from sqlmodel import Session, select
from ulid import ULID

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, FactAtomic, MemoryCheckpoint, MemoryItem
from lantai.services.record_ops_service import sync_vector_upsert
from lantai.storage import db
from lantai.storage.fts import sync_fts

# 肯定与否定词库模式
_AFFIRMATIVE_PATTERNS = [
    r"是",
    r"对",
    r"没错",
    r"确实",
    r"好的",
    r"更改了",
    r"已变更",
    r"变了",
    r"成了",
    r"改用",
    r"换成",
    r"yes",
    r"yep",
    r"correct",
    r"right",
    r"true",
]
_NEGATIVE_PATTERNS = [
    r"不是",
    r"不对",
    r"没有",
    r"写错",
    r"别改",
    r"未变",
    r"还是",
    r"依然",
    r"没变",
    r"保持",
    r"no",
    r"nope",
    r"false",
    r"wrong",
]


def detect_memory_probes(
    query: str | None = None,
    session_id: str | None = None,
    session: Session | None = None,
    principal=None,
    *,
    db_session: Session | None = None,
    memory_ids: list[str] | None = None,
) -> list[dict]:
    """扫描未决冲突账本与模糊置信度事实，生成主动求证探针（ADR-0037 探颐）。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 4）：此前
    `select(ConflictEvent).where(status=="open")` 全表捞，把别人的
    `existing_content` + `incoming_ref` **两份全文**吐出来，还渲染成
    "顺便向您求证确认下：关于「…」"的问句——等于把 B 的记忆原文
    包装成给 A 的提问。

    `ConflictEvent` 没有归属列，判据过渡推导：`conf.memory_id → MemoryItem.user_id`。
    口径同 `resolve_probe_response`：admin/system 全权；非 admin 只见自己
    记忆上的冲突。NULL 属主的老行**不可见**（宁可漏探，不可错探——探针
    会把正文渲染成问句送给用户，那是比列表更直接的泄漏面）。

    `principal=None`（`cli/mcp.py` 的 `handle_probe_detect`）不加过滤，
    与改动前逐字一致。
    """
    if not settings.ENABLE_PROACTIVE_PROBING:
        return []

    active_session = db_session if db_session is not None else session

    clean_q = (query or "").strip()
    if not clean_q and not memory_ids:
        return []

    tokens = (
        set(w.strip() for w in jieba.lcut(clean_q) if len(w.strip()) >= 2) if clean_q else set()
    )

    def _visible(s: Session, conf: ConflictEvent) -> bool:
        """冲突对当前 principal 是否可见（过渡到挂载记忆的属主）。"""
        if principal is None:
            return True
        if bool(getattr(principal, "is_admin", False)):
            return True
        item = s.get(MemoryItem, conf.memory_id)
        if item is None:
            # 挂载记忆已不存在：账本本身无归属可判，不泄露任何正文
            return False
        from lantai.services.work_item_service import _viewer_of

        return bool(item.user_id) and item.user_id == _viewer_of(principal)

    def _detect(s: Session) -> list[dict]:
        open_conflicts = s.exec(select(ConflictEvent).where(ConflictEvent.status == "open")).all()

        probes = []
        for conf in open_conflicts:
            if not _visible(s, conf):
                continue
            item = s.get(MemoryItem, conf.memory_id)
            existing_content = item.content if item else ""
            incoming_ref = conf.incoming_ref or ""

            # 判断当前查询是否涉及冲突的主题词，或指定了 memory_ids
            all_text = f"{existing_content} {incoming_ref}"
            text_tokens = set(w.strip() for w in jieba.lcut(all_text) if len(w.strip()) >= 2)

            match_query = (not tokens) or bool(tokens & text_tokens)
            match_memory_id = bool(memory_ids and conf.memory_id in memory_ids)

            if match_query or match_memory_id:
                question = (
                    f"顺便向您求证确认下：关于「{existing_content[:30]}」，"
                    f"目前是否已变更为「{incoming_ref[:30]}」？"
                )
                probes.append(
                    {
                        "probe_id": f"probe_{conf.id}",
                        "conflict_id": conf.id,
                        "memory_id": conf.memory_id,
                        "existing_content": existing_content,
                        "incoming_ref": incoming_ref,
                        "question": question,
                        "suggested_action": "resolve_conflict",
                    }
                )

        # 2. 扫描临界模糊带事实/记忆（0.3 <= confidence <= 0.6）
        # 归属过滤原则：非 admin 只探查自身属主记录
        viewer = None
        if principal is not None and not bool(getattr(principal, "is_admin", False)):
            from lantai.services.work_item_service import _viewer_of

            viewer = _viewer_of(principal)

        # 已经生成过冲突解决探针的 memory_id 集合（优先冲突解决，不产生同条重复探针）
        conflicted_mem_ids = {conf.memory_id for conf in open_conflicts if conf.status == "open"}

        # 2a. 临界模糊记忆项（MemoryItem）
        mem_query = (
            select(MemoryItem)
            .where(MemoryItem.status == "active")
            .where(MemoryItem.confidence >= 0.3)
            .where(MemoryItem.confidence <= 0.6)
        )
        if viewer:
            mem_query = mem_query.where(MemoryItem.user_id == viewer)
        fuzzy_memories = s.exec(mem_query).all()

        for m in fuzzy_memories:
            if m.id in conflicted_mem_ids:
                continue
            m_text = m.content or ""
            m_tokens = set(w.strip() for w in jieba.lcut(m_text) if len(w.strip()) >= 2)
            match_q = (not tokens) or bool(tokens & m_tokens)
            match_mid = bool(memory_ids and m.id in memory_ids)

            if match_q or match_mid:
                question = f"顺便向您求证确认下：关于「{m_text[:30]}」，该事实是否准确？"
                probes.append(
                    {
                        "probe_id": f"probe_{m.id}",
                        "conflict_id": None,
                        "memory_id": m.id,
                        "existing_content": m_text,
                        "incoming_ref": "",
                        "question": question,
                        "suggested_action": "clarify_fact",
                    }
                )

        # 2b. 临界模糊原子事实（FactAtomic）
        fact_query = (
            select(FactAtomic)
            .where(FactAtomic.confidence >= 0.3)
            .where(FactAtomic.confidence <= 0.6)
        )
        if viewer:
            fact_query = fact_query.where(FactAtomic.user_id == viewer)
        fuzzy_facts = s.exec(fact_query).all()

        for f in fuzzy_facts:
            if f.source_memory_id and f.source_memory_id in conflicted_mem_ids:
                continue
            f_text = f.statement or ""
            f_tokens = set(w.strip() for w in jieba.lcut(f_text) if len(w.strip()) >= 2)
            match_q = (not tokens) or bool(tokens & f_tokens)
            match_mid = bool(memory_ids and f.source_memory_id in memory_ids)

            if match_q or match_mid:
                question = f"顺便向您求证确认下：关于「{f_text[:30]}」，该事实是否属实？"
                probes.append(
                    {
                        "probe_id": f"probe_{f.id}",
                        "conflict_id": None,
                        "memory_id": f.source_memory_id,
                        "existing_content": f_text,
                        "incoming_ref": "",
                        "question": question,
                        "suggested_action": "clarify_fact",
                    }
                )

        logger.info("探颐：针对查询 '%s' 检出 %d 个主动探针", clean_q[:20], len(probes))
        return probes

    if active_session is not None:
        return _detect(active_session)
    with db.get_session() as s:
        return _detect(s)


def format_probing_context(probes: list[dict]) -> str:
    """将探针列表格式化为可注入 Prompt 的【探颐·待求证事项】上下文块。"""
    if not probes:
        return ""
    lines = ["【探颐·待求证事项】（若当前对话语境适宜，建议在回复末尾顺带向用户确认下述事实）："]
    for p in probes:
        item_id = p.get("conflict_id") or p.get("probe_id")
        lines.append(f"- 探针提示 [ID: {item_id}]: {p['question']}")
    return "\n".join(lines)


def _is_affirmative_reply(reply: str) -> bool:
    """整句确认才算肯定:以肯定词开头,其后接结束、标点或"的"等确认语气,而非另一个词。"""
    body = re.sub(r"^[\s。,.!?~!?,、]+|[\s。,.!?~!?,、]+$", "", reply)
    for pat in _AFFIRMATIVE_PATTERNS:
        if re.match(rf"(?:{pat})(?:$|[\s,.!?~,。!?、的啊呀吧呢])", body, re.IGNORECASE):
            return True
    return False


def resolve_probe_response(
    conflict_id: str,
    user_reply: str,
    session: Session | None = None,
    principal=None,
) -> dict:
    """分析用户对探针的自然语言答复，自动执行冲突消解与版本更替。

    归属校验（票 .scratch/readside-gaps/07 修法口径 4）：**全轮最严重的一条**。
    肯定分支会 `item.content = conf.incoming_ref`——写的是正文，而此前一个
    身份都不取，A 用自己的 key 就能把 B 的记忆原文覆盖掉（`OBSERVED3.txt`
    实测：`mem-user-B.content` 被改成 A 传的 `incoming_ref`）。

    `ConflictEvent` **没有归属列**（`tables.py:371-383`），判据只能过渡推导：
    `conf.memory_id → MemoryItem.user_id`。这比加列更准——冲突账本的存在意义
    就是挂在某条记忆上，记忆换属主，冲突的可见性跟着换。

    按**破坏性写操作**处理，复用 `acl.ensure_can_delete` 单一真源（同票 02
    口径）：admin/system 全权；非 admin 只能消解自己记忆上的冲突。
    403 必须发生在任何 `s.commit()` 之前——不允许"拒绝了但已经落库"。

    `principal=None`（`cli/mcp.py` 的 `handle_probe_resolve` 没有调用方身份）
    不加校验，与改动前逐字一致；内部入口与 REST 暴露面分开。
    """
    clean_reply = user_reply.strip()
    if not clean_reply:
        return {"status": "error", "message": "user_reply 不能为空"}

    def _resolve(s: Session) -> dict:
        conf = s.get(ConflictEvent, conflict_id)
        if not conf:
            return {"status": "not_found", "message": f"ConflictEvent {conflict_id} 未找到"}

        if conf.status != "open":
            return {"status": "already_resolved", "conflict_status": conf.status}

        item = s.get(MemoryItem, conf.memory_id)
        if not item:
            return {"status": "error", "message": f"MemoryItem {conf.memory_id} 不存在"}

        # 归属校验：在任何写操作之前（403 不能伴随落库）
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=item.user_id,
                resource_tenant_id=item.tenant_id,
                lane=item.lane,
            )

        # 1. 优先匹配否定词（否定意图优先）
        is_neg = any(re.search(pat, clean_reply, re.IGNORECASE) for pat in _NEGATIVE_PATTERNS)
        is_aff = _is_affirmative_reply(clean_reply)

        if is_neg:
            conf.status = "dismissed"
            conf.resolved_by = "proactive_probe_user_rejected"
            conf.resolved_at = utcnow()
            s.add(conf)
            s.commit()
            logger.info("探颐：用户否定探针，已废弃冲突 %s", conflict_id)
            return {"status": "resolved", "action": "dismissed", "conflict_id": conflict_id}

        elif is_aff:
            # 记录 Checkpoint 快照
            before_snapshot = item.model_dump(mode="json")
            item.content = conf.incoming_ref
            item.version += 1
            item.decay_score = 1.0
            item.updated_at = utcnow()

            cp = MemoryCheckpoint(
                id=f"cp_{ULID()}",
                memory_id=item.id,
                version=item.version,
                before=before_snapshot,
                after=item.model_dump(mode="json"),
                trigger="proactive_probe_resolved",
                created_at=utcnow(),
            )
            s.add(cp)

            conf.status = "resolved"
            conf.resolved_by = "proactive_probe_user_confirmed"
            conf.resolved_at = utcnow()
            s.add(conf)
            s.add(item)

            # FTS 同事务强一致（ADR-0008，同 correct_memory / promoter update 口径，
            # 票 .scratch/review-fixes-2026-10/01）：正文已替换而 FTS 停留旧文 =
            # 可命中旧事实的脏索引。sync_fts 异常随本事务回滚——正文停在旧值、
            # 冲突保持 open（宁 miss 不脏写），不再吞成 warning。
            sync_fts(s, item.id, item.content)

            s.commit()

            # 向量库独立于 SQL 事务：对**替换后的新正文**重建（票 01 修前 import 自
            # 不存在的 lantai.retrieval.embed 且 embed 的是 item.key 而非新正文）。
            # 复用 record_ops_service 的 best-effort 重同步原语（embed + 8 键
            # metadata 契约 + 失败如实回报），失败不静默：回执如实暴露（宁 miss
            # 不脏写——正文已改而索引失败必须在返回体/warning 里看得见）。
            vector_synced = sync_vector_upsert(item.id, item)
            warnings: list[str] = []
            if not vector_synced:
                warnings.append("vector resync failed; content updated in SQL/FTS only")
                logger.warning("探颐：消解后向量重同步失败（正文与 FTS 已更新）memory=%s", item.id)

            logger.info("探颐：用户肯定探针，已成功消解冲突并更新记忆 %s", item.id)
            return {
                "status": "resolved",
                "action": "applied",
                "conflict_id": conflict_id,
                "memory_id": item.id,
                "vector_synced": vector_synced,
                "warnings": warnings,
            }

        else:
            logger.info("探颐：用户答复未明确表态，保持冲突待审状态")
            return {"status": "deferred", "action": "pending", "conflict_id": conflict_id}

    if session is not None:
        return _resolve(session)
    with db.get_session() as s:
        return _resolve(s)
