"""探颐（ADR-0037）：认知层主动求证探针引擎 (TanyiProbingEngine)

当检索召回命中未决冲突（ConflictEvent）或低置信度模糊事实（0.3 <= confidence <= 0.6）时，
自动生成具备上下文感知、归属安全隔离的主动求证插桩（ProbeInsertion），注入到 Agent Prompt，
促使 Agent 在回复中温和求证，贯彻「宁 miss 不脏写」与主动消歧闭环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jieba
from sqlmodel import Session

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.models.tables import ConflictEvent, MemoryItem


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
