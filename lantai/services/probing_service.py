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
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, MemoryCheckpoint, MemoryItem
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage import db

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
    query: str,
    session_id: str | None = None,
    session: Session | None = None,
    principal=None,
) -> list[dict]:
    """扫描未决冲突账本，检测与当前查询相关的冲突事实，生成主动求证探针。

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
    clean_q = query.strip()
    if not clean_q:
        return []

    tokens = set(w.strip() for w in jieba.lcut(clean_q) if len(w.strip()) >= 2)

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

            # 判断当前查询是否涉及冲突的主题词
            all_text = f"{existing_content} {incoming_ref}"
            text_tokens = set(w.strip() for w in jieba.lcut(all_text) if len(w.strip()) >= 2)

            # 如果查询词与冲突内容有交集，或者查询直接命中实体
            if not tokens or (tokens & text_tokens):
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
                    }
                )

        logger.info("探颐：针对查询 '%s' 检出 %d 个主动探针", clean_q[:20], len(probes))
        return probes

    if session is not None:
        return _detect(session)
    with db.get_session() as s:
        return _detect(s)


def format_probing_context(probes: list[dict]) -> str:
    """将探针列表格式化为可注入 Prompt 的【探颐·待求证事项】上下文块。"""
    if not probes:
        return ""
    lines = ["【探颐·待求证事项】（若当前对话语境适宜，建议在回复末尾顺带向用户确认下述事实）："]
    for p in probes:
        lines.append(f"- 探针提示 [ID: {p['conflict_id']}]: {p['question']}")
    return "\n".join(lines)


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
        is_aff = any(re.search(pat, clean_reply, re.IGNORECASE) for pat in _AFFIRMATIVE_PATTERNS)

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
            s.commit()

            # 同步更新索引
            try:
                from lantai.retrieval.embed import embed

                emb = embed([item.key])[0]
                index_memory_item(
                    item.id,
                    emb,
                    {
                        "key": item.key,
                        "memory_type": item.memory_type,
                        "lane": getattr(item, "lane", "general") or "general",
                        "domain": getattr(item, "domain", "user") or "user",
                        "tenant_id": getattr(item, "tenant_id", "") or "",
                        "user_id": getattr(item, "user_id", "") or "",
                        "session_id": getattr(item, "session_id", "") or "",
                        "agent_id": getattr(item, "agent_id", "") or "",
                    },
                )
            except Exception as exc:
                logger.warning("探颐：索引更新异常（数据已落库）: %s", exc)

            logger.info("探颐：用户肯定探针，已成功消解冲突并更新记忆 %s", item.id)
            return {
                "status": "resolved",
                "action": "applied",
                "conflict_id": conflict_id,
                "memory_id": item.id,
            }

        else:
            logger.info("探颐：用户答复未明确表态，保持冲突待审状态")
            return {"status": "deferred", "action": "pending", "conflict_id": conflict_id}

    if session is not None:
        return _resolve(session)
    with db.get_session() as s:
        return _resolve(s)
