"""探颐（ADR-0037）：认知层主动求证探针引擎 (TanyiProbingEngine)

当检索召回命中未决冲突（ConflictEvent）或低置信度模糊事实（0.3 <= confidence <= 0.6）时，
自动生成具备上下文感知、归属安全隔离的主动求证插桩（ProbeInsertion），注入到 Agent Prompt，
促使 Agent 在回复中温和求证，贯彻「宁 miss 不脏写」与主动消歧闭环。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import jieba
from sqlmodel import Session
from ulid import ULID

from lantai.core.acl import ensure_can_delete
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, MemoryCheckpoint, MemoryItem
from lantai.services.record_ops_service import sync_vector_upsert
from lantai.storage.fts import sync_fts


class ResolutionIntent(StrEnum):
    AFFIRMATIVE = "affirmative"
    NEGATIVE = "negative"
    CORRECTION = "correction"
    AMBIGUOUS = "ambiguous"


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
    r"改了",
    r"改好了",
    r"确认",
    r"确有此事",
    r"属实",
    r"准确",
    r"yes",
    r"yep",
    r"yeah",
    r"correct",
    r"right",
    r"true",
]

_NEGATIVE_PATTERNS = [
    r"不是",
    r"不对",
    r"没有",
    r"写错",
    r"搞错",
    r"弄错",
    r"别改",
    r"未变",
    r"还是",
    r"依然",
    r"没变",
    r"保持",
    r"不属实",
    r"不准确",
    r"假",
    r"no",
    r"nope",
    r"false",
    r"wrong",
]

_CORRECTION_TRIGGER_PATTERNS = [
    r"(?:其实|实际上|而是|转到了|换成了|选用了|搬到了|改成|改为)\s*(.+)$",
]


def parse_user_resolution_intent(reply: str) -> tuple[ResolutionIntent, str]:
    """严谨解析用户对探颐求证探针的自然语言答复。

    返回: (ResolutionIntent, 修正事实内容)
    """
    clean_reply = (reply or "").strip()
    if not clean_reply:
        return ResolutionIntent.AMBIGUOUS, ""

    body = re.sub(r"^[\s。,.!?~!?,、]+|[\s。,.!?~!?,、]+$", "", clean_reply)

    # 1. 检查是否有针对性修正语句（例如：“不是A，其实搬到了杭州滨江”、“最后选用了 PostgreSQL 数据库”）
    for pat in _CORRECTION_TRIGGER_PATTERNS:
        match = re.search(pat, body)
        if match:
            correction_text = match.group(1).strip()
            # 清理末尾标点
            correction_text = re.sub(r"[\s。,.!?~!?,、]+$", "", correction_text)
            if len(correction_text) >= 2:
                return ResolutionIntent.CORRECTION, correction_text

    # 2. 检查否定意图优先
    is_neg = any(re.search(pat, body, re.IGNORECASE) for pat in _NEGATIVE_PATTERNS)
    if is_neg:
        return ResolutionIntent.NEGATIVE, ""

    # 3. 检查肯定语气（句首确认或含有明确肯定词）
    for pat in _AFFIRMATIVE_PATTERNS:
        if re.search(
            rf"(?:^|[\s，,。!?~、])(?:{pat})(?:$|[\s,.!?~,。!?、的啊呀吧呢了]|变更|变了)",
            body,
            re.IGNORECASE,
        ):
            return ResolutionIntent.AFFIRMATIVE, ""

    return ResolutionIntent.AMBIGUOUS, ""


def execute_tanyi_resolution(
    conflict_id: str,
    user_reply: str,
    session: Session,
    principal=None,
) -> dict[str, Any]:
    """执行探颐主动求证自动闭环消解。

    肯定：确认新主张并更新正文与 Checkpoint 快照；
    否定：废弃冲突保留原主张；
    修正：按用户提供的新事实写入正文；
    暧昧：deferred 保持原样（宁 miss 不脏写）。
    """
    conf = session.get(ConflictEvent, conflict_id)
    if not conf:
        return {"status": "error", "message": f"ConflictEvent {conflict_id} 不存在"}

    if conf.status != "open":
        return {"status": "already_resolved", "conflict_status": conf.status}

    item = session.get(MemoryItem, conf.memory_id)
    if not item:
        return {"status": "error", "message": f"MemoryItem {conf.memory_id} 不存在"}

    # 严格行级写权限检查（403 阻断）
    if principal is not None:
        ensure_can_delete(
            principal,
            resource_user_id=item.user_id,
            resource_tenant_id=item.tenant_id,
            lane=item.lane,
        )

    intent, correction = parse_user_resolution_intent(user_reply)

    if intent == ResolutionIntent.NEGATIVE:
        conf.status = "dismissed"
        conf.resolved_by = "tanyi_proactive_user_rejected"
        conf.resolved_at = utcnow()
        session.add(conf)
        session.commit()
        logger.info("探颐：用户否定探针，已废弃冲突 %s", conflict_id)
        return {
            "status": "resolved",
            "action": "dismissed",
            "intent": intent,
            "conflict_id": conflict_id,
            "memory_id": item.id,
        }

    if intent in (ResolutionIntent.AFFIRMATIVE, ResolutionIntent.CORRECTION):
        before_snapshot = item.model_dump(mode="json")
        applied_content = (
            correction if intent == ResolutionIntent.CORRECTION else (conf.incoming_ref or "")
        )

        item.content = applied_content
        item.version = (item.version or 1) + 1
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
        session.add(cp)

        conf.status = "resolved"
        conf.resolved_by = "tanyi_proactive_user_confirmed"
        conf.resolved_at = utcnow()
        session.add(conf)
        session.add(item)

        # 同步全文与向量（容错 SQLite 内存测试无 FTS 表的场景）
        try:
            sync_fts(session, item.id, item.content)
        except Exception as e:
            logger.warning("探颐：FTS 表未初始化或同步失败，跳过: %s", e)
        session.commit()

        vector_synced = sync_vector_upsert(item.id, item)
        logger.info(
            "探颐：用户意图 %s 已闭环消解冲突 %s，更新记忆 %s",
            intent,
            conflict_id,
            item.id,
        )
        return {
            "status": "resolved",
            "action": "applied_correction" if intent == ResolutionIntent.CORRECTION else "applied",
            "intent": intent,
            "conflict_id": conflict_id,
            "memory_id": item.id,
            "vector_synced": vector_synced,
        }

    # 模糊暧昧，不脏写
    logger.info("探颐：用户答复表态模糊暧昧，保持冲突待审状态")
    return {
        "status": "deferred",
        "action": "pending",
        "intent": ResolutionIntent.AMBIGUOUS,
        "conflict_id": conflict_id,
        "memory_id": item.id,
    }


@dataclass
class ProbeInsertion:
    """探颐上下文插桩实体。"""

    probe_id: str
    target_id: str  # 对应 memory_id
    question: str
    suggested_action: str  # "resolve_conflict" | "clarify_fact" | "verify_low_confidence"
    confidence: float = 0.5
    conflict_id: str | None = None
    existing_content: str = ""
    incoming_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "target_id": self.target_id,
            "memory_id": self.target_id,
            "question": self.question,
            "suggested_action": self.suggested_action,
            "confidence": self.confidence,
            "conflict_id": self.conflict_id,
            "existing_content": self.existing_content,
            "incoming_ref": self.incoming_ref,
            "metadata": self.metadata,
        }


class TanyiProbingEngine:
    """探颐主动求证认知引擎。"""

    def __init__(self, min_fuzzy_conf: float = 0.3, max_fuzzy_conf: float = 0.6):
        self.min_fuzzy_conf = min_fuzzy_conf
        self.max_fuzzy_conf = max_fuzzy_conf

    def generate_probes(
        self,
        query: str = "",
        recalled_memories: list[MemoryItem | dict] | None = None,
        unresolved_conflicts: list[ConflictEvent] | None = None,
        session: Session | None = None,
        principal=None,
        limit: int = 5,
    ) -> list[ProbeInsertion]:
        """依据当前 query、召回记忆与未决冲突集合，生成主动求证探针。"""
        if not getattr(settings, "ENABLE_PROACTIVE_PROBING", True):
            return []

        clean_q = (query or "").strip()
        tokens = (
            set(w.strip() for w in jieba.lcut(clean_q) if len(w.strip()) >= 2) if clean_q else set()
        )

        def _is_owner_visible(mem: MemoryItem | dict | None) -> bool:
            """严格租户与属主安全隔离：非管理员只能看见同租户且属于自己的记忆。"""
            if mem is None:
                return False
            if principal is None:
                return True
            if bool(getattr(principal, "is_admin", False)):
                return True

            t_id = mem.tenant_id if isinstance(mem, MemoryItem) else mem.get("tenant_id")
            p_tenant = getattr(principal, "tenant_id", None)
            if p_tenant and t_id and str(p_tenant) != str(t_id):
                return False

            u_id = mem.user_id if isinstance(mem, MemoryItem) else mem.get("user_id")
            from lantai.services.work_item_service import _viewer_of

            viewer = _viewer_of(principal)
            return not (u_id and str(u_id) != str(viewer))

        probes: list[ProbeInsertion] = []
        conflicted_mem_ids: set[str] = set()

        # 1. 优先处理未决冲突 (ConflictEvent)
        if unresolved_conflicts:
            for conf in unresolved_conflicts:
                if conf.status != "open":
                    continue

                mem_item = None
                if session is not None and conf.memory_id:
                    mem_item = session.get(MemoryItem, conf.memory_id)

                if mem_item is not None and not _is_owner_visible(mem_item):
                    continue

                existing_content = mem_item.content if mem_item else ""
                incoming_ref = conf.incoming_ref or ""

                text_to_match = f"{existing_content} {incoming_ref}"
                text_tokens = set(
                    w.strip() for w in jieba.lcut(text_to_match) if len(w.strip()) >= 2
                )
                if tokens and not (tokens & text_tokens):
                    continue

                question = (
                    f"顺便向您求证确认下：关于「{existing_content[:30]}」，"
                    f"目前是否已变更为「{incoming_ref[:30]}」？"
                )

                probes.append(
                    ProbeInsertion(
                        probe_id=f"probe_conf_{conf.id}",
                        target_id=conf.memory_id,
                        question=question,
                        suggested_action="resolve_conflict",
                        confidence=mem_item.confidence
                        if mem_item and mem_item.confidence is not None
                        else 0.5,
                        conflict_id=conf.id,
                        existing_content=existing_content,
                        incoming_ref=incoming_ref,
                        metadata={"rule_name": conf.rule_name},
                    )
                )
                conflicted_mem_ids.add(conf.memory_id)
                if len(probes) >= limit:
                    break

        # 2. 处理低置信度模糊记忆 (0.3 <= confidence <= 0.6)
        if recalled_memories and len(probes) < limit:
            for m in recalled_memories:
                m_id = m.id if isinstance(m, MemoryItem) else m.get("id")
                if not m_id or m_id in conflicted_mem_ids:
                    continue

                if not _is_owner_visible(m):
                    continue

                m_conf = m.confidence if isinstance(m, MemoryItem) else m.get("confidence")
                if m_conf is None:
                    continue

                if self.min_fuzzy_conf <= m_conf <= self.max_fuzzy_conf:
                    m_content = m.content if isinstance(m, MemoryItem) else m.get("content", "")
                    m_tokens = set(w.strip() for w in jieba.lcut(m_content) if len(w.strip()) >= 2)
                    if tokens and not (tokens & m_tokens):
                        continue

                    question = f"顺便向您求证确认下：关于「{m_content[:30]}」，该事实是否属实准确？"

                    probes.append(
                        ProbeInsertion(
                            probe_id=f"probe_mem_{m_id}",
                            target_id=m_id,
                            question=question,
                            suggested_action="clarify_fact",
                            confidence=round(m_conf, 3),
                            conflict_id=None,
                            existing_content=m_content,
                            incoming_ref="",
                            metadata={
                                "lane": m.lane if isinstance(m, MemoryItem) else m.get("lane")
                            },
                        )
                    )
                    if len(probes) >= limit:
                        break

        logger.info(
            "探颐引擎：为查询 '%s' 检出 %d 条主动求证插桩探针",
            clean_q[:20],
            len(probes),
        )
        return probes

    def format_context(self, probes: list[ProbeInsertion]) -> str:
        """格式化为注入 Agent 对话上下文的 Markdown 块。"""
        if not probes:
            return ""

        lines = [
            "【探颐·待求证事项】（若当前对话语境适宜，建议在回复末尾顺带向用户确认下述事实）："
        ]
        for p in probes:
            ref_id = p.conflict_id or p.probe_id
            lines.append(f"- 探针提示 [ID: {ref_id}]: {p.question}")
        return "\n".join(lines)
