"""格物（ADR-0013 / Gewu）：认知层因果时序拓扑与因果回溯引擎 (CausalEngine)。

突破单点静态事实记忆的局限，提供面向 Agent 决策分析的因果拓扑推演、
前置根因溯源（root cause analysis）与事件时序叙事构建能力。
内置访问控制隔离（ACL）与闭环死循环熔断保护。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

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

    def attribute_decision(
        self,
        memory_id: str | None = None,
        query: str | None = None,
        max_depth: int = 10,
    ) -> dict[str, Any]:
        """高阶决策因果归因入口，定位根因并输出正序事件脉络。"""
        return self.get_provenance(
            memory_id=memory_id,
            query=query,
            max_depth=max_depth,
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

    def _check_node_access(self, memory_id: str) -> bool:
        """检查调用者对节点的访问权限。"""
        if self.principal is None or bool(getattr(self.principal, "is_admin", False)):
            return True
        viewer = viewer_of(self.principal)
        with db.get_session() as s:
            mem = s.get(MemoryItem, memory_id)
            if mem is None or mem.status != "active":
                return False
            return (mem.user_id or None) in (None, viewer)

    def evaluate_counterfactual(
        self,
        target_id: str,
        intervened_node_id: str,
        max_depth: int = 10,
    ) -> dict[str, Any]:
        """反事实因果判定：如果阻断 intervened_node_id，目标 target_id 是否仍可由其他路径达成。

        返回：
        - found: 是否找到目标且具有访问权限
        - target_id: 目标节点 ID
        - intervened_node_id: 被阻断的介入节点 ID
        - is_necessary: 是否为绝对必要根因（阻断后下游完全断裂）
        - reachable_without_node: 阻断后是否依然可达
        - alternative_paths: 替代存活因果路径列表（若有）
        - narrative: 自然语言解释
        """
        if not self._check_node_access(target_id):
            return {
                "found": False,
                "target_id": target_id,
                "intervened_node_id": intervened_node_id,
                "is_necessary": False,
                "reachable_without_node": False,
                "alternative_paths": [],
                "narrative": f"目标节点 {target_id} 不存在或无访问权限",
            }

        if target_id == intervened_node_id:
            return {
                "found": True,
                "target_id": target_id,
                "intervened_node_id": intervened_node_id,
                "is_necessary": True,
                "reachable_without_node": False,
                "alternative_paths": [],
                "narrative": f"目标节点自身即为介入阻断节点 {intervened_node_id}，阻断后直接消除。",
            }

        # 逆向搜索通往 target_id 的所有路径（忽略被介入阻断的节点）
        # 路径由 CausalHop 字典组成
        viewer = None
        if self.principal is not None and not bool(getattr(self.principal, "is_admin", False)):
            viewer = viewer_of(self.principal)

        def _get_parent_hops(node: str) -> list[dict[str, Any]]:
            hops = []
            with db.get_session() as s:
                # 1. node 作为 target，被其他 source causes/leads_to
                stmt1 = select(MemoryEdge).where(
                    MemoryEdge.target_memory_id == node,
                    MemoryEdge.relation.in_(["causes", "leads_to"]),
                )
                for edge in s.exec(stmt1).all():
                    parent_mem = s.get(MemoryItem, edge.source_memory_id)
                    if parent_mem is not None and parent_mem.status == "active":
                        if viewer is None or (parent_mem.user_id or None) in (None, viewer):
                            hops.append(
                                {
                                    "edge_id": edge.id,
                                    "from_id": edge.source_memory_id,
                                    "to_id": edge.target_memory_id,
                                    "relation": edge.relation,
                                    "confidence": edge.confidence,
                                }
                            )
                # 2. node 作为 source，triggered_by 指向前置 target
                stmt2 = select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == node,
                    MemoryEdge.relation == "triggered_by",
                )
                for edge in s.exec(stmt2).all():
                    parent_mem = s.get(MemoryItem, edge.target_memory_id)
                    if parent_mem is not None and parent_mem.status == "active":
                        if viewer is None or (parent_mem.user_id or None) in (None, viewer):
                            hops.append(
                                {
                                    "edge_id": edge.id,
                                    "from_id": edge.target_memory_id,
                                    "to_id": edge.source_memory_id,
                                    "relation": edge.relation,
                                    "confidence": edge.confidence,
                                }
                            )
            return hops

        # 收集所有不含 intervened_node_id 的有效路径
        # BFS / DFS 逆向回溯
        from collections import deque

        queue = deque([([target_id], [])])  # (visited_nodes, path_hops)
        surviving_paths: list[list[dict[str, Any]]] = []

        while queue:
            current_chain, current_hops = queue.popleft()
            if len(current_hops) >= max_depth:
                if current_hops:
                    surviving_paths.append(list(reversed(current_hops)))
                continue

            curr_node = current_chain[-1]
            parents = _get_parent_hops(curr_node)
            # 过滤掉介入节点
            valid_parents = [
                h
                for h in parents
                if h["from_id"] != intervened_node_id and h["from_id"] not in current_chain
            ]

            if not valid_parents:
                if current_hops:
                    surviving_paths.append(list(reversed(current_hops)))
            else:
                for phop in valid_parents:
                    queue.append((current_chain + [phop["from_id"]], current_hops + [phop]))

        if not surviving_paths:
            return {
                "found": True,
                "target_id": target_id,
                "intervened_node_id": intervened_node_id,
                "is_necessary": True,
                "reachable_without_node": False,
                "alternative_paths": [],
                "narrative": f"反事实推演：若阻断前置节点 {intervened_node_id}，目标 {target_id} 的因果链将完全断裂，证实该节点为必要根因。",
            }

        return {
            "found": True,
            "target_id": target_id,
            "intervened_node_id": intervened_node_id,
            "is_necessary": False,
            "reachable_without_node": True,
            "alternative_paths": surviving_paths,
            "narrative": f"反事实推演：即使阻断节点 {intervened_node_id}，目标 {target_id} 仍可通过 {len(surviving_paths)} 条替代路径达成。",
        }

    def project_causal_impact(
        self,
        node_id: str,
        max_depth: int = 5,
        min_confidence: float = 0.05,
    ) -> dict[str, Any]:
        """前瞻模拟从 node_id 出发可能引发的下游次生事件及影响面。

        计算波及深度与累积置信度衰减，防环路死锁。
        """
        if not self._check_node_access(node_id):
            return {
                "found": False,
                "start_node_id": node_id,
                "impacted_nodes": [],
                "max_depth_reached": 0,
            }

        viewer = None
        if self.principal is not None and not bool(getattr(self.principal, "is_admin", False)):
            viewer = viewer_of(self.principal)

        impacted: dict[str, dict[str, Any]] = {}
        # queue: (current_node_id, current_depth, cumulative_confidence)
        from collections import deque

        queue = deque([(node_id, 0, 1.0)])
        visited_nodes = {node_id}

        max_reached = 0

        while queue:
            curr_id, depth, cum_conf = queue.popleft()
            if depth >= max_depth:
                continue

            # 寻找下游 causes / leads_to 边
            with db.get_session() as s:
                stmt = select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == curr_id,
                    MemoryEdge.relation.in_(["causes", "leads_to"]),
                )
                edges = s.exec(stmt).all()
                for edge in edges:
                    target_mem = s.get(MemoryItem, edge.target_memory_id)
                    if target_mem is None or target_mem.status != "active":
                        continue
                    if viewer is not None and (target_mem.user_id or None) not in (None, viewer):
                        continue

                    next_id = edge.target_memory_id
                    next_conf = cum_conf * (edge.confidence or 0.5)
                    if next_conf < min_confidence:
                        continue

                    next_depth = depth + 1
                    max_reached = max(max_reached, next_depth)

                    if (
                        next_id not in impacted
                        or next_conf > impacted[next_id]["cumulative_confidence"]
                    ):
                        impacted[next_id] = {
                            "node_id": next_id,
                            "content": target_mem.content,
                            "depth": next_depth,
                            "cumulative_confidence": next_conf,
                            "relation": edge.relation,
                        }

                    if next_id not in visited_nodes:
                        visited_nodes.add(next_id)
                        queue.append((next_id, next_depth, next_conf))

        sorted_impacted = sorted(
            impacted.values(), key=lambda x: (x["depth"], -x["cumulative_confidence"])
        )
        return {
            "found": True,
            "start_node_id": node_id,
            "impacted_nodes": sorted_impacted,
            "max_depth_reached": max_reached,
        }

    def find_critical_causal_path(
        self,
        target_id: str,
        max_depth: int = 10,
    ) -> dict[str, Any]:
        """在 DAG 因果网络中计算通向 target_id 的最高置信度主导关键因果路径。"""
        if not self._check_node_access(target_id):
            return {
                "found": False,
                "target_id": target_id,
                "critical_path": [],
                "path_confidence": 0.0,
                "root_id": None,
            }

        viewer = None
        if self.principal is not None and not bool(getattr(self.principal, "is_admin", False)):
            viewer = viewer_of(self.principal)

        def _get_upstream_edges(node: str) -> list[dict[str, Any]]:
            hops = []
            with db.get_session() as s:
                stmt1 = select(MemoryEdge).where(
                    MemoryEdge.target_memory_id == node,
                    MemoryEdge.relation.in_(["causes", "leads_to"]),
                )
                for edge in s.exec(stmt1).all():
                    parent_mem = s.get(MemoryItem, edge.source_memory_id)
                    if parent_mem is not None and parent_mem.status == "active":
                        if viewer is None or (parent_mem.user_id or None) in (None, viewer):
                            hops.append(
                                {
                                    "edge_id": edge.id,
                                    "from_id": edge.source_memory_id,
                                    "to_id": edge.target_memory_id,
                                    "relation": edge.relation,
                                    "confidence": edge.confidence or 0.5,
                                }
                            )
                stmt2 = select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == node,
                    MemoryEdge.relation == "triggered_by",
                )
                for edge in s.exec(stmt2).all():
                    parent_mem = s.get(MemoryItem, edge.target_memory_id)
                    if parent_mem is not None and parent_mem.status == "active":
                        if viewer is None or (parent_mem.user_id or None) in (None, viewer):
                            hops.append(
                                {
                                    "edge_id": edge.id,
                                    "from_id": edge.target_memory_id,
                                    "to_id": edge.source_memory_id,
                                    "relation": edge.relation,
                                    "confidence": edge.confidence or 0.5,
                                }
                            )
            return hops

        # 遍历所有路径计算置信度乘积
        all_paths: list[tuple[float, list[dict[str, Any]], str]] = []
        from collections import deque

        queue = deque([([target_id], [], 1.0)])

        while queue:
            visited_chain, hops_so_far, conf = queue.popleft()
            if len(hops_so_far) >= max_depth:
                if hops_so_far:
                    all_paths.append((conf, list(reversed(hops_so_far)), visited_chain[-1]))
                continue

            curr = visited_chain[-1]
            up_hops = _get_upstream_edges(curr)
            valid_up = [h for h in up_hops if h["from_id"] not in visited_chain]

            if not valid_up:
                if hops_so_far:
                    all_paths.append((conf, list(reversed(hops_so_far)), curr))
            else:
                for h in valid_up:
                    queue.append(
                        (visited_chain + [h["from_id"]], hops_so_far + [h], conf * h["confidence"])
                    )

        if not all_paths:
            return {
                "found": True,
                "target_id": target_id,
                "critical_path": [],
                "path_confidence": 1.0,
                "root_id": target_id,
            }

        # 挑选置信度最高的路径
        best_conf, best_path, root_id = max(all_paths, key=lambda x: x[0])
        return {
            "found": True,
            "target_id": target_id,
            "critical_path": best_path,
            "path_confidence": best_conf,
            "root_id": root_id,
        }
