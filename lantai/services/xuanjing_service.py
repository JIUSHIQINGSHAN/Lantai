"""悬镜（Xuanjing）记忆星图拓扑数据聚合服务（ADR-0059）。

为 3D 神经元记忆星图与力导向拓扑提供高效、安全的数据聚合：
- 节点多维渲染：衰减亮度（decay_score）、质量引力（weight）、生命周期与同袍作用域；
- 关系拓扑边：涵盖格物因果拓扑边（causes/triggered_by/leads_to）与支持/反驳/演化边；
- 记忆星系聚簇：MemoryScene 场景与 lane 聚簇；
- 严格租户 ACL 隔离与同袍（Tongpao）跨 Agent 公共知识殿堂放行。
"""

import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, or_, select

from lantai.core.acl import viewer_of
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryScene
from lantai.storage import db

LABEL_MAX = 48


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _label_from_text(content: str) -> str:
    content = (content or "").strip().replace("\n", " ")
    if len(content) > LABEL_MAX:
        return content[:LABEL_MAX] + "…"
    return content


def _calculate_node_weight(item: MemoryItem) -> float:
    """计算节点在 3D/力导向图中的物理权重与引力质量。"""
    importance = float(item.importance or 0.5)
    use_count = int(item.use_count or 0)
    helpful_count = int(item.helpful_count or 0)
    # 基础权重 + 对数使用频率增益 + 有用性回馈增益
    return round(1.0 + (importance * 1.5) + math.log1p(use_count) + (0.3 * helpful_count), 3)


def build_xuanjing_topology(
    session: Session,
    limit: int = 250,
    scene_id: str | None = None,
    lane: str | None = None,
    include_archived: bool = False,
    principal=None,
) -> dict[str, Any]:
    """构建悬镜记忆星图拓扑数据结构。"""
    stmt = select(MemoryItem)
    if not include_archived:
        stmt = stmt.where(MemoryItem.status == "active")

    if scene_id:
        stmt = stmt.where(MemoryItem.scene_id == scene_id)
    if lane:
        stmt = stmt.where(MemoryItem.lane == lane)

    # 租户与行级 ACL 隔离（同袍机制放行）
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        # 1. 租户隔离
        if getattr(principal, "tenant_id", None):
            stmt = stmt.where(MemoryItem.tenant_id == principal.tenant_id)

        # 2. 用户归属（NULL 或 viewer）
        if getattr(principal, "user_id", None):
            v_user = viewer_of(principal)
            stmt = stmt.where(
                or_(
                    MemoryItem.user_id == v_user,
                    MemoryItem.user_id.is_(None),
                )
            )

        # 3. Agent 隔离与同袍公共殿堂放行
        if getattr(principal, "agent_id", None):
            v_agent = principal.agent_id
            stmt = stmt.where(
                or_(
                    MemoryItem.agent_id == v_agent,
                    MemoryItem.agent_id.is_(None),
                    MemoryItem.shared_scope == "swarm_shared",
                )
            )

        if principal.allowed_lanes is not None:
            from sqlmodel import col

            stmt = stmt.where(col(MemoryItem.lane).in_(principal.allowed_lanes))
    elif principal is None:
        v_user = viewer_of(None)
        stmt = stmt.where(
            or_(
                MemoryItem.user_id == v_user,
                MemoryItem.user_id.is_(None),
            )
        )

    stmt = stmt.order_by(MemoryItem.updated_at.desc()).limit(limit)
    memories = session.exec(stmt).all()
    mem_ids = {m.id for m in memories}

    if not mem_ids:
        return {
            "generated_at": _now_iso(),
            "nodes": [],
            "edges": [],
            "clusters": [],
            "stats": {"node_count": 0, "edge_count": 0, "lane_counts": {}, "relation_counts": {}},
        }

    # 查询涉及候选记忆的拓扑边
    edge_stmt = select(MemoryEdge).where(
        or_(
            MemoryEdge.source_memory_id.in_(mem_ids),
            MemoryEdge.target_memory_id.in_(mem_ids),
        )
    )
    if principal is not None and getattr(principal, "tenant_id", None):
        edge_stmt = edge_stmt.where(
            or_(
                MemoryEdge.tenant_id == principal.tenant_id,
                MemoryEdge.tenant_id.is_(None),
            )
        )
    raw_edges = session.exec(edge_stmt).all()

    # 仅保留两端都在过滤后候选集中的边
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for e in raw_edges:
        if e.source_memory_id not in mem_ids or e.target_memory_id not in mem_ids:
            continue
        edge_key = (e.source_memory_id, e.target_memory_id, e.relation)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            {
                "id": e.id,
                "source": e.source_memory_id,
                "target": e.target_memory_id,
                "relation": e.relation,
                "confidence": round(float(e.confidence or 0.0), 3),
            }
        )

    # 构筑节点列表
    nodes: list[dict[str, Any]] = []
    for m in memories:
        label = _label_from_text(m.content or "") or (m.key or m.id)
        decay = round(float(m.decay_score if m.decay_score is not None else 1.0), 3)
        weight = _calculate_node_weight(m)
        nodes.append(
            {
                "id": m.id,
                "label": label,
                "lane": m.lane,
                "tier": m.tier,
                "decay_score": decay,
                "decay_class": m.decay_class,
                "weight": weight,
                "importance": round(float(m.importance or 0.5), 3),
                "scene_id": m.scene_id,
                "shared_scope": getattr(m, "shared_scope", "private"),
                "lifecycle_status": getattr(m, "lifecycle_status", "active"),
                "agent_id": m.agent_id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            }
        )

    # 聚合星系与场景聚簇
    scene_ids = {m.scene_id for m in memories if m.scene_id}
    scenes: dict[str, MemoryScene] = {}
    if scene_ids:
        sc_list = session.exec(select(MemoryScene).where(MemoryScene.id.in_(scene_ids))).all()
        scenes = {sc.id: sc for sc in sc_list}

    # 统计聚簇信息
    cluster_map: dict[str, dict[str, Any]] = {}
    for n in nodes:
        sc_id = n.get("scene_id")
        if sc_id:
            c_key = sc_id
            if c_key not in cluster_map:
                sc_name = scenes[sc_id].name if sc_id in scenes else sc_id
                cluster_map[c_key] = {
                    "id": sc_id,
                    "type": "scene",
                    "name": sc_name,
                    "count": 0,
                    "lanes": set(),
                }
            cluster_map[c_key]["count"] += 1
            cluster_map[c_key]["lanes"].add(n["lane"])

    clusters: list[dict[str, Any]] = []
    for c in cluster_map.values():
        clusters.append(
            {
                "id": c["id"],
                "type": c["type"],
                "name": c["name"],
                "count": c["count"],
                "lanes": sorted(list(c["lanes"])),
            }
        )

    return {
        "generated_at": _now_iso(),
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "lane_counts": dict(Counter(n["lane"] for n in nodes)),
            "relation_counts": dict(Counter(e["relation"] for e in edges)),
        },
    }


def get_xuanjing_graph(
    limit: int = 250,
    scene_id: str | None = None,
    lane: str | None = None,
    include_archived: bool = False,
    principal=None,
) -> dict[str, Any]:
    """打开数据库会话获取悬镜拓扑。"""
    with db.get_session() as session:
        return build_xuanjing_topology(
            session=session,
            limit=limit,
            scene_id=scene_id,
            lane=lane,
            include_archived=include_archived,
            principal=principal,
        )
