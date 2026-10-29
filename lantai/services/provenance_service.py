"""格物（Gewu · v0.31）：事件溯源链查询与决策因果归因闭环服务。

依据 docs/future-roadmap.md 方向二「格物」：
- trace_decision_provenance: 针对目标决策/状态变更，回溯因果拓扑定位根因与演化脉络；
- get_event_timeline: 聚合因果边关联事件与时序时间线，正序推演事态发展。
"""

from datetime import UTC, datetime, timezone

from sqlmodel import select

from lantai.core.acl import viewer_of
from lantai.models.tables import MemoryItem
from lantai.services.edge_service import get_causal_chain
from lantai.storage import db


def _is_accessible(mem: MemoryItem | None, viewer: str | None) -> bool:
    if mem is None:
        return False
    if viewer is None:
        return True
    return (mem.user_id or None) in (None, viewer)


def trace_decision_provenance(
    memory_id: str | None = None,
    query: str | None = None,
    max_depth: int = 10,
    principal=None,
) -> dict:
    """溯源决策/事态诱因与根因脉络。

    输入支持：
    1. memory_id: 直接指定目标决策记忆 ID
    2. query: 自然语言模糊检索目标决策（取最高分活跃记忆）

    输出包含：
    - found: 是否找到目标记忆并成功溯源
    - target_id: 目标决策记忆 ID
    - root_cause_id: 最顶层前置根因记忆 ID
    - causal_chain: 逐跳逆向因果链
    - timeline: 按发生时间正序排列的因果事件时间线
    - narrative: 自然语言因果脉络摘要
    """
    viewer = None
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        viewer = viewer_of(principal)

    target_item: MemoryItem | None = None

    with db.get_session() as s:
        if memory_id:
            item = s.get(MemoryItem, memory_id)
            if item is not None and item.status == "active" and _is_accessible(item, viewer):
                target_item = item
        elif query:
            # 优先从数据库根据内容匹配候选
            stmt = select(MemoryItem).where(MemoryItem.status == "active")
            all_items = s.exec(stmt).all()
            accessible = [m for m in all_items if _is_accessible(m, viewer)]

            # 语义/关键词重合度匹配选出最佳 target
            import re

            q_chars = set(re.findall(r"[\w]", query.lower()))
            best_score = -1.0
            best_match = None
            for m in accessible:
                m_chars = set(re.findall(r"[\w]", m.content.lower()))
                overlap = len(q_chars & m_chars)
                ratio = overlap / len(q_chars) if q_chars else 0.0
                if ratio > best_score and ratio > 0.3:
                    best_score = ratio
                    best_match = m

            target_item = best_match

    if target_item is None:
        return {
            "found": False,
            "target_id": None,
            "root_cause_id": None,
            "causal_chain": [],
            "timeline": [],
            "narrative": "未找到匹配的目标决策或无权访问。",
        }

    # 沿 backward 方向逆查因果链
    chain_res = get_causal_chain(
        target_item.id, direction="backward", max_depth=max_depth, principal=principal
    )
    causal_hops = chain_res.get("chain", [])

    # 收集链上所有节点记忆
    node_ids = [target_item.id]
    for hop in causal_hops:
        if hop["to_id"] not in node_ids:
            node_ids.append(hop["to_id"])

    # 取出各节点详情以构建时间线与摘要
    nodes_map: dict[str, MemoryItem] = {}
    with db.get_session() as s:
        for nid in node_ids:
            m = s.get(MemoryItem, nid)
            if m and _is_accessible(m, viewer):
                nodes_map[nid] = m

    # 确定根因节点（逆向链最后一跳的 to_id）
    root_cause_id = target_item.id
    if causal_hops:
        last_to = causal_hops[-1]["to_id"]
        if last_to in nodes_map:
            root_cause_id = last_to

    # 构建时间线（按 event_time 或 created_at 排序）
    timeline_items = []
    for nid, m in nodes_map.items():
        ts = m.event_time or m.created_at
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        timeline_items.append(
            {
                "memory_id": nid,
                "content": m.content,
                "timestamp": ts.isoformat() if ts else None,
                "_sort_ts": ts or datetime.min.replace(tzinfo=UTC),
            }
        )

    timeline_items.sort(key=lambda x: x["_sort_ts"])
    for item in timeline_items:
        item.pop("_sort_ts", None)

    # 自动生成自然语言脉络叙事
    narrative_parts = []
    if root_cause_id != target_item.id and root_cause_id in nodes_map:
        root_content = nodes_map[root_cause_id].content
        target_content = target_item.content
        narrative_parts.append(f"溯源分析结论：目标决策「{target_content}」")
        narrative_parts.append(f"其前序初始根因可追溯至「{root_content}」。")
        if len(causal_hops) > 1:
            narrative_parts.append(f"经过中间共 {len(causal_hops)} 级因果链路逐步传导演进。")
    else:
        narrative_parts.append(f"目标决策「{target_item.content}」未检索到更深层的前置诱因。")

    return {
        "found": True,
        "target_id": target_item.id,
        "root_cause_id": root_cause_id,
        "causal_chain": causal_hops,
        "timeline": timeline_items,
        "narrative": "".join(narrative_parts),
    }


def get_event_timeline(
    memory_id: str | None = None,
    max_depth: int = 10,
    principal=None,
) -> dict:
    """获取与特定节点相关的时序事件流（包含前序诱因与后续演进事态）。"""
    viewer = None
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        viewer = viewer_of(principal)

    if not memory_id:
        return {"events": []}

    with db.get_session() as s:
        root = s.get(MemoryItem, memory_id)
        if not root or not _is_accessible(root, viewer):
            return {"events": []}

    # 正向与逆向因果链双向扩展
    fwd = get_causal_chain(memory_id, direction="forward", max_depth=max_depth, principal=principal)
    bwd = get_causal_chain(
        memory_id, direction="backward", max_depth=max_depth, principal=principal
    )

    all_ids = {memory_id}
    for hop in fwd.get("chain", []):
        all_ids.add(hop["from_id"])
        all_ids.add(hop["to_id"])
    for hop in bwd.get("chain", []):
        all_ids.add(hop["from_id"])
        all_ids.add(hop["to_id"])

    events = []
    with db.get_session() as s:
        for mid in all_ids:
            mem = s.get(MemoryItem, mid)
            if mem and _is_accessible(mem, viewer):
                ts = mem.event_time or mem.created_at
                if ts is not None and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                events.append(
                    {
                        "memory_id": mem.id,
                        "content": mem.content,
                        "timestamp": ts.isoformat() if ts else None,
                        "_sort_ts": ts or datetime.min.replace(tzinfo=UTC),
                    }
                )

    events.sort(key=lambda x: x["_sort_ts"])
    for e in events:
        e.pop("_sort_ts", None)

    return {"events": events}


def evaluate_counterfactual_service(
    target_id: str,
    intervened_node_id: str,
    max_depth: int = 10,
    principal=None,
) -> dict:
    """格物（v0.40）：反事实因果判定服务，推演当 intervened_node_id 被阻断时 target 是否仍可达成。"""
    from lantai.cognition.causal import CausalEngine

    engine = CausalEngine(principal=principal)
    return engine.evaluate_counterfactual(
        target_id=target_id,
        intervened_node_id=intervened_node_id,
        max_depth=max_depth,
    )


def project_causal_impact_service(
    memory_id: str,
    max_depth: int = 5,
    min_confidence: float = 0.05,
    principal=None,
) -> dict:
    """格物（v0.40）：前瞻决策影响面模拟服务，推演次生波及面与因果强度衰减。"""
    from lantai.cognition.causal import CausalEngine

    engine = CausalEngine(principal=principal)
    return engine.project_causal_impact(
        node_id=memory_id,
        max_depth=max_depth,
        min_confidence=min_confidence,
    )
