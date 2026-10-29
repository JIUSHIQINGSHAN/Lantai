"""格物（ADR-0013 / Gewu）：认知层因果时序拓扑与因果回溯引擎 (CausalEngine)。

突破单点静态事实记忆的局限，提供面向 Agent 决策分析的因果拓扑推演、
前置根因溯源（root cause analysis）与事件时序叙事构建能力。
内置访问控制隔离（ACL）与闭环死循环熔断保护。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from lantai.core.acl import viewer_of
from lantai.core.logger import logger
from lantai.models.tables import CAUSAL_RELATIONS, MemoryEdge, MemoryItem
from lantai.services.edge_service import add_edge, get_causal_chain
from lantai.services.provenance_service import get_event_timeline, trace_decision_provenance
from lantai.storage import db


@dataclass
class CausalHop:
    """因果跳跃单步实体。"""

    edge_id: str
    from_id: str
    to_id: str
    relation: str
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation": self.relation,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class CausalEngine:
    """格物·因果拓扑与事件溯源认知引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def link_causality(
        self,
        source_id: str,
        target_id: str,
        relation: str = "causes",
        confidence: float = 0.8,
        reason: str = "",
    ) -> dict[str, Any]:
        """建立两条记忆之间的因果拓扑关联边。

        支持关系：causes, triggered_by, leads_to。
        """
        if relation not in CAUSAL_RELATIONS:
            raise ValueError(f"非法因果关系 '{relation}'，必须为 {sorted(CAUSAL_RELATIONS)} 之一")

        return add_edge(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            confidence=confidence,
            principal=self.principal,
        )

    def trace_chain(
        self,
        memory_id: str,
        direction: str = "forward",
        max_depth: int = 10,
    ) -> list[CausalHop]:
        """沿拓扑方向推演因果链。

        - forward: 顺查下游引发的次生事件 (causes / leads_to)
        - backward: 逆查前置诱因与触发根因 (triggered_by / 逆向 causes)
        """
        res = get_causal_chain(
            memory_id=memory_id,
            direction=direction,
            max_depth=max_depth,
            principal=self.principal,
        )
        hops = []
        for raw in res.get("chain", []):
            hops.append(
                CausalHop(
                    edge_id=raw.get("edge_id", ""),
                    from_id=raw.get("from_id", ""),
                    to_id=raw.get("to_id", ""),
                    relation=raw.get("relation", ""),
                    confidence=raw.get("confidence", 0.5),
                )
            )
        return hops

    def get_provenance(
        self,
        memory_id: str | None = None,
        query: str | None = None,
        max_depth: int = 10,
    ) -> dict[str, Any]:
        """执行结构化决策因果溯源，定位根因节点并生成正序时间线。"""
        return trace_decision_provenance(
            memory_id=memory_id,
            query=query,
            max_depth=max_depth,
            principal=self.principal,
        )

    def get_timeline(self, memory_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        """获取与目标节点相关的因果事件全景正序时间线。"""
        res = get_event_timeline(
            memory_id=memory_id,
            max_depth=max_depth,
            principal=self.principal,
        )
        return res.get("events", [])

    def format_causal_context(self, memory_id: str, max_depth: int = 5) -> str:
        """为 Agent 对话生成结构化 Markdown 因果溯源提示块。"""
        prov = self.get_provenance(memory_id=memory_id, max_depth=max_depth)
        if not prov.get("found"):
            return ""

        lines = [
            "【格物·因果决策脉络】",
            f"- 目标节点: {prov.get('target_id')}",
            f"- 溯源推论: {prov.get('narrative')}",
        ]
        timeline = prov.get("timeline", [])
        if timeline:
            lines.append("- 时序演进脉络:")
            for item in timeline:
                ts = item.get("timestamp") or "未知时间"
                lines.append(f"  * [{ts}] {item.get('content')}")
        return "\n".join(lines)
