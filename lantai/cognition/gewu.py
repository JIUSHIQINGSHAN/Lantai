"""格物：受限因果归因与双时间轴事件投影（ADR-0061）。"""

from __future__ import annotations

import heapq
import json
from collections import deque
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.time import ensure_aware, iso_utc, utcnow
from lantai.models.tables import (
    ConversationJob,
    ConversationMessage,
    MemoryEdge,
    MemoryItem,
    MemoryProposal,
)
from lantai.storage import db

COUNTERFACTUAL_PATH_CAP = 64
COUNTERFACTUAL_NODE_BUDGET = 512
COUNTERFACTUAL_MAX_DEPTH_LIMIT = 10


class GewuEngine:
    """一次归因共用一个会话；显式 Session 由调用方持有，不关闭、不提交。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _session(self):
        return nullcontext(self.session) if self.session is not None else db.get_session()

    def _scope(self, model=MemoryItem) -> list:
        principal = self.principal
        if principal is None or principal.is_admin:
            return []
        tenant = getattr(principal, "tenant_id", None) or ""
        agent = getattr(principal, "agent_id", None)
        owner = viewer_of(principal)
        clauses = [func.coalesce(model.tenant_id, "") == tenant]
        clauses.append(
            or_(
                and_(
                    func.coalesce(model.shared_scope, "private") == "private",
                    model.user_id == owner,
                    or_(model.agent_id.is_(None), model.agent_id == agent),
                ),
                and_(
                    model.shared_scope == "swarm_shared",
                    or_(model.user_id.is_(None), model.user_id == owner),
                ),
            )
        )
        lanes = getattr(principal, "allowed_lanes", None)
        if lanes is not None:
            clauses.append(model.lane.in_(lanes))
        return clauses

    def _visible(self, as_of, as_of_recorded) -> list:
        clauses = [MemoryItem.status == "active", *self._scope()]
        clauses.extend(
            [
                or_(MemoryItem.valid_from.is_(None), MemoryItem.valid_from <= as_of),
                or_(MemoryItem.valid_to.is_(None), MemoryItem.valid_to > as_of),
                or_(MemoryItem.event_time.is_(None), MemoryItem.event_time <= as_of),
            ]
        )
        if as_of_recorded is not None:
            clauses.append(MemoryItem.created_at <= as_of_recorded)
        return clauses

    def trace_decision_provenance(
        self,
        target_id: str,
        max_depth: int = 5,
        *,
        as_of=None,
        as_of_recorded=None,
    ) -> dict[str, Any]:
        """反向 BFS 枚举简单根因路径，帽外路径不参与关键路径择优。

        triggered_by 的存储方向是结果→诱因；输出统一为诱因→结果。
        扩展、驻留队列和邻接行均有硬界；深度或资源界截断不冒充根因。
        """
        if type(max_depth) is not int or not 1 <= max_depth <= COUNTERFACTUAL_MAX_DEPTH_LIMIT:
            raise ValueError("max_depth 必须是 1..10 的整数")
        as_of = ensure_aware(as_of) or utcnow()
        as_of_recorded = ensure_aware(as_of_recorded)
        result = dict(
            found=False,
            target_id=target_id,
            root_causes=[],
            paths=[],
            critical_path=[],
            path_confidence=0.0,
            paths_truncated=False,
            cycles_detected=False,
            nodes_expanded=0,
            timeline=[],
        )
        with self._session() as session:
            visible = self._visible(as_of, as_of_recorded)
            target = session.exec(
                select(MemoryItem).where(MemoryItem.id == target_id, *visible)
            ).first()
            if target is None:
                return result
            result["found"] = True
            queue = deque([([target_id], [], 1.0)])
            admitted = {target_id}
            while queue:
                if len(result["paths"]) >= COUNTERFACTUAL_PATH_CAP:
                    result["paths_truncated"] = True
                    break
                if result["nodes_expanded"] >= COUNTERFACTUAL_NODE_BUDGET:
                    result["paths_truncated"] = True
                    break
                nodes, hops, confidence = queue.popleft()
                current = nodes[-1]
                result["nodes_expanded"] += 1
                parents = session.exec(
                    select(MemoryEdge, MemoryItem)
                    .join(
                        MemoryItem,
                        or_(
                            and_(
                                MemoryEdge.target_memory_id == current,
                                MemoryEdge.relation.in_(["causes", "leads_to"]),
                                MemoryItem.id == MemoryEdge.source_memory_id,
                            ),
                            and_(
                                MemoryEdge.source_memory_id == current,
                                MemoryEdge.relation == "triggered_by",
                                MemoryItem.id == MemoryEdge.target_memory_id,
                            ),
                        ),
                    )
                    .where(
                        *visible,
                        *([MemoryEdge.created_at <= as_of_recorded] if as_of_recorded else []),
                    )
                    .order_by(MemoryEdge.id)
                    .limit(COUNTERFACTUAL_NODE_BUDGET + 1)
                ).all()
                if len(parents) > COUNTERFACTUAL_NODE_BUDGET:
                    result["paths_truncated"] = True
                    parents = parents[:COUNTERFACTUAL_NODE_BUDGET]
                if not parents:
                    result["paths"].append(
                        dict(
                            node_ids=list(reversed(nodes)),
                            hops=list(reversed(hops)),
                            confidence=confidence,
                        )
                    )
                    continue
                for edge, parent in parents:
                    if parent.id in nodes:
                        result["cycles_detected"] = True
                        continue
                    if len(hops) >= max_depth:
                        result["paths_truncated"] = True
                        continue
                    if len(queue) >= COUNTERFACTUAL_NODE_BUDGET or (
                        parent.id not in admitted and len(admitted) >= COUNTERFACTUAL_NODE_BUDGET
                    ):
                        result["paths_truncated"] = True
                        break
                    admitted.add(parent.id)
                    hop = dict(
                        edge_id=edge.id,
                        from_id=parent.id,
                        to_id=current,
                        relation=edge.relation,
                        confidence=edge.confidence,
                    )
                    queue.append((nodes + [parent.id], hops + [hop], confidence * edge.confidence))
            paths = result["paths"]
            result["root_causes"] = sorted({p["node_ids"][0] for p in paths})
            if paths:
                best = max(paths, key=lambda p: p["confidence"])
                result["critical_path"] = best["node_ids"]
                result["path_confidence"] = best["confidence"]
            timeline = self._project(session, sorted(admitted), as_of, as_of_recorded, max_depth)
            result["timeline"] = timeline["events"]
            result["timeline_truncated"] = timeline["timeline_truncated"]
            result["cycles_detected"] |= timeline["cycles_detected"]
            result["temporal_conflicts"] = timeline["temporal_conflicts"]
        return result

    def format_causal_context(
        self,
        target_id: str,
        max_depth: int = 5,
        *,
        as_of: datetime | None = None,
        as_of_recorded: datetime | None = None,
    ) -> str:
        """将结构化归因作为数据围栏内容输出，禁止将记忆文本拼成指令。"""
        from lantai.llm.fence import wrap_as_data

        result = self.trace_decision_provenance(
            target_id, max_depth, as_of=as_of, as_of_recorded=as_of_recorded
        )
        if not result["found"]:
            return ""
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        return "【格物·因果归因】\n" + wrap_as_data(content, item_id=target_id)

    def project_event_timeline(
        self,
        memory_ids: list[str],
        max_depth: int = 5,
        *,
        as_of=None,
        as_of_recorded=None,
    ) -> dict[str, Any]:
        """投影明确指定的可见记忆及其作业/裁决，最多输出 512 个事件。

        时间不被拓扑排序改写。物理时钟与因果顺序冲突、循环及裁剪均外显。
        未建立明确关联的作业不参与投影，不根据时间或相似内容推测关联。
        """
        if type(max_depth) is not int or not 1 <= max_depth <= COUNTERFACTUAL_MAX_DEPTH_LIMIT:
            raise ValueError("max_depth 必须是 1..10 的整数")
        with self._session() as session:
            return self._project(
                session,
                memory_ids,
                ensure_aware(as_of) or utcnow(),
                ensure_aware(as_of_recorded),
                max_depth,
            )

    def _expand_timeline_ids(
        self, session, seed_ids, visible, max_depth, as_of_recorded
    ) -> tuple[list[str], bool]:
        discovered = set(seed_ids)
        queue = deque((memory_id, 0) for memory_id in seed_ids)
        expanded = 0
        truncated = False
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            if expanded >= COUNTERFACTUAL_NODE_BUDGET:
                truncated = True
                break
            expanded += 1
            neighbors = session.exec(
                select(MemoryEdge, MemoryItem)
                .join(
                    MemoryItem,
                    or_(
                        and_(
                            MemoryEdge.source_memory_id == current,
                            MemoryItem.id == MemoryEdge.target_memory_id,
                        ),
                        and_(
                            MemoryEdge.target_memory_id == current,
                            MemoryItem.id == MemoryEdge.source_memory_id,
                        ),
                    ),
                )
                .where(
                    or_(
                        MemoryEdge.source_memory_id == current,
                        MemoryEdge.target_memory_id == current,
                    ),
                    MemoryEdge.relation.in_(["causes", "leads_to", "triggered_by"]),
                    *visible,
                    *([MemoryEdge.created_at <= as_of_recorded] if as_of_recorded else []),
                )
                .order_by(MemoryEdge.id)
                .limit(COUNTERFACTUAL_NODE_BUDGET + 1)
            ).all()
            if len(neighbors) > COUNTERFACTUAL_NODE_BUDGET:
                truncated = True
                neighbors = neighbors[:COUNTERFACTUAL_NODE_BUDGET]
            for _edge, neighbor in neighbors:
                if neighbor.id in discovered:
                    continue
                if len(discovered) >= COUNTERFACTUAL_NODE_BUDGET:
                    truncated = True
                    break
                discovered.add(neighbor.id)
                queue.append((neighbor.id, depth + 1))
        return sorted(discovered), truncated

    def _related_scope(self, row) -> bool:
        if self.principal is None or self.principal.is_admin:
            return True
        return (
            (row.tenant_id or "") == (getattr(self.principal, "tenant_id", None) or "")
            and row.user_id == viewer_of(self.principal)
            and (row.agent_id or None) in (None, getattr(self.principal, "agent_id", None))
        )

    def _project(self, session, memory_ids, as_of, as_of_recorded, max_depth) -> dict[str, Any]:
        budget = COUNTERFACTUAL_NODE_BUDGET
        ids = sorted(set(memory_ids))
        truncated = len(ids) > budget
        seed_ids = ids[:budget]
        visible = self._visible(as_of, as_of_recorded)
        memories = session.exec(
            select(MemoryItem).where(MemoryItem.id.in_(seed_ids), *visible).order_by(MemoryItem.id)
        ).all()
        ids, expansion_truncated = self._expand_timeline_ids(
            session, [memory.id for memory in memories], visible, max_depth, as_of_recorded
        )
        truncated |= expansion_truncated
        memories = session.exec(
            select(MemoryItem).where(MemoryItem.id.in_(ids), *visible).order_by(MemoryItem.id)
        ).all()
        events = {}
        for memory in memories:
            timestamp = memory.event_time or memory.valid_from or memory.created_at
            events[memory.id] = dict(
                event_id=memory.id,
                timestamp=iso_utc(timestamp),
                recorded_at=iso_utc(memory.created_at),
                valid_from=iso_utc(memory.valid_from),
                valid_to=iso_utc(memory.valid_to),
                actor=(
                    f"agent:{memory.agent_id}"
                    if memory.agent_id
                    else ("user" if memory.user_id else "system")
                ),
                event_type="ingest",
                summary=memory.content,
                action="ingest",
                trigger="",
                outcome="active",
                incoming_causes=[],
                outgoing_effects=[],
            )
        if not events:
            return dict(
                events=[],
                timeline_truncated=truncated,
                cycles_detected=False,
                temporal_conflicts=[],
            )
        edges = session.exec(
            select(MemoryEdge)
            .where(
                MemoryEdge.source_memory_id.in_(events),
                MemoryEdge.target_memory_id.in_(events),
                MemoryEdge.relation.in_(["causes", "leads_to", "triggered_by"]),
                *([MemoryEdge.created_at <= as_of_recorded] if as_of_recorded else []),
            )
            .order_by(MemoryEdge.id)
            .limit(budget + 1)
        ).all()
        truncated |= len(edges) > budget
        for edge in edges[:budget]:
            cause, effect = edge.source_memory_id, edge.target_memory_id
            if edge.relation == "triggered_by":
                cause, effect = effect, cause
            if effect not in events[cause]["outgoing_effects"]:
                events[cause]["outgoing_effects"].append(effect)
                events[effect]["incoming_causes"].append(cause)

        # 裁决以真实 decided_at 为准；仅创建提案不冒充已裁决事件。
        proposals = session.exec(
            select(MemoryProposal)
            .where(
                MemoryProposal.target_memory_id.in_(events),
                MemoryProposal.decided_at.is_not(None),
                MemoryProposal.decided_at <= as_of,
                *(
                    [
                        MemoryProposal.created_at <= as_of_recorded,
                        MemoryProposal.decided_at <= as_of_recorded,
                    ]
                    if as_of_recorded
                    else []
                ),
            )
            .order_by(MemoryProposal.id)
            .limit(budget + 1)
        ).all()
        truncated |= len(proposals) > budget
        for proposal in proposals[:budget]:
            if not self._related_scope(proposal):
                continue
            if proposal.id in events:
                raise ValueError("事件标识冲突")
            if len(events) >= budget:
                truncated = True
                break
            events[proposal.id] = dict(
                event_id=proposal.id,
                timestamp=iso_utc(proposal.decided_at),
                recorded_at=iso_utc(proposal.created_at),
                actor=(f"agent:{proposal.agent_id}" if proposal.agent_id else proposal.decided_by),
                event_type="decision",
                summary=proposal.decision_reason,
                action=proposal.proposal_type,
                trigger=proposal.target_memory_id,
                outcome=proposal.status,
                incoming_causes=[],
                outgoing_effects=[],
            )

        # 显式来源指针是关联证据，消息行的权限仍需独立验证。
        seen_jobs = set()
        for memory in memories:
            message_id = (memory.provenance or {}).get("conversation_message_id")
            if not isinstance(message_id, str) or not message_id:
                continue
            message = session.get(ConversationMessage, message_id)
            if message is None or message.status != "stored" or not self._related_scope(message):
                continue
            if ensure_aware(message.occurred_at or message.received_at) > as_of:
                continue
            if as_of_recorded and ensure_aware(message.received_at) > as_of_recorded:
                continue
            job = session.exec(
                select(ConversationJob).where(
                    ConversationJob.message_id == message_id,
                    *([ConversationJob.created_at <= as_of_recorded] if as_of_recorded else []),
                )
            ).first()
            if job is None or job.id in seen_jobs:
                continue
            seen_jobs.add(job.id)
            if job.id in events:
                raise ValueError("事件标识冲突")
            if len(events) >= budget:
                truncated = True
                break
            events[job.id] = dict(
                event_id=job.id,
                timestamp=iso_utc(message.occurred_at or job.created_at),
                recorded_at=iso_utc(job.created_at),
                actor=(f"agent:{message.agent_id}" if message.role == "assistant" else "user"),
                event_type="execution",
                summary=message.content,
                action="process_conversation",
                trigger=message.id,
                outcome=job.status,
                incoming_causes=[],
                outgoing_effects=[],
            )

        # Kahn 偏序：无因果约束的节点以发生时间、记录时间和标识稳定排序。
        indegree = {eid: len(event["incoming_causes"]) for eid, event in events.items()}

        def key(eid):
            event = events[eid]
            return (event["timestamp"], event["recorded_at"], eid)

        ready = [key(eid) for eid, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        ordered = []
        while ready:
            _, _, eid = heapq.heappop(ready)
            ordered.append(eid)
            for effect in sorted(events[eid]["outgoing_effects"]):
                indegree[effect] -= 1
                if indegree[effect] == 0:
                    heapq.heappush(ready, key(effect))
        cyclic = len(ordered) != len(events)
        if cyclic:
            emitted = set(ordered)
            ordered.extend(sorted((eid for eid in events if eid not in emitted), key=key))
        conflicts = []
        for eid, event in events.items():
            event["incoming_causes"].sort()
            event["outgoing_effects"].sort()
            for effect in event["outgoing_effects"]:
                if event["timestamp"] > events[effect]["timestamp"]:
                    conflicts.append([eid, effect])
        return dict(
            events=[events[eid] for eid in ordered],
            timeline_truncated=truncated,
            cycles_detected=cyclic,
            temporal_conflicts=sorted(conflicts),
        )
