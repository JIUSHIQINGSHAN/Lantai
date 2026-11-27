"""悬镜（Xuanjing）记忆星图拓扑数据聚合服务（ADR-0059）。

为 3D 神经元记忆星图与力导向拓扑提供高效、安全的数据聚合：
- 节点多维渲染：衰减亮度（decay_score）、质量引力（weight）、生命周期与同袍作用域；
- 关系拓扑边：涵盖格物因果拓扑边（causes/triggered_by/leads_to）与支持/反驳/演化边；
- 记忆星系聚簇：MemoryScene 场景与 lane 聚簇；
- 严格租户 ACL 隔离与同袍（Tongpao）跨 Agent 公共知识殿堂放行。
"""

import base64
import json
import math
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func
from sqlmodel import Session, col, or_, select

from lantai.core.acl import federation_scope_clause, viewer_of
from lantai.core.time import ensure_aware, iso_utc, parse_iso_utc, utcnow
from lantai.memory.decay_class import decay_multiplier
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryScene
from lantai.storage import db

LABEL_MAX = 48
DEFAULT_CURVE_DAYS = (0, 1, 7, 30, 90, 180)


def _now_iso() -> str:
    return iso_utc(utcnow())


def _clamp_score(value: float) -> float:
    score = float(value)
    return max(0.0, min(1.0, score)) if math.isfinite(score) else 0.0


def calculate_decay_curve(
    decay_class: str, base_score: float, days: tuple[int, ...] = DEFAULT_CURVE_DAYS
) -> list[dict[str, float | int]]:
    """Return an Ebbinghaus projection using the project's canonical half-lives."""
    base = _clamp_score(base_score)
    out: list[dict[str, float | int]] = []
    for raw_days in days:
        day = max(0, int(raw_days))
        score = round(_clamp_score(base * decay_multiplier(decay_class, day)), 3)
        out.append({"days": day, "score": score})
    return out


def calculate_halo(decay_score: float, weight: float) -> dict[str, float]:
    """Map decay and activity into bounded halo parameters for topology clients."""
    decay = _clamp_score(decay_score)
    raw_mass = float(weight)
    mass = max(0.0, raw_mass) if math.isfinite(raw_mass) else 0.0
    intensity = max(0.0, min(1.0, decay * mass / 2.0))
    radius = min(64.0, 4.0 * math.sqrt(2.0 * decay * mass))
    opacity = max(0.12, min(0.9, intensity))
    return {
        "radius": round(radius, 3),
        "intensity": round(intensity, 3),
        "opacity": round(opacity, 3),
    }


def _encode_cursor(
    updated_at: datetime,
    item_id: str = "",
    *,
    source: str = "memory",
    watermark: datetime | None = None,
) -> str:
    payload = {
        "updated_at": iso_utc(updated_at),
        "id": item_id,
        "source": source,
        "watermark": iso_utc(watermark or updated_at),
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str, str, datetime] | None:
    if not cursor:
        return None
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        updated_at = parse_iso_utc(str(payload["updated_at"]))
        item_id = payload.get("id") or ""
        source = payload.get("source") or "memory"
        watermark = parse_iso_utc(str(payload.get("watermark") or payload["updated_at"]))
        if (
            not isinstance(item_id, str)
            or not isinstance(source, str)
            or source not in ("edge", "memory", "~")
        ):
            raise ValueError
        return updated_at, item_id, source, watermark
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("invalid cursor") from exc


def _json_tag_filter(tag: str):
    values = func.json_each(MemoryItem.tags).table_valued("value").alias("memory_tags")
    return exists(select(1).select_from(values).where(values.c.value == tag))


def _apply_scope(stmt, principal):
    if bool(getattr(principal, "is_admin", False)):
        return stmt
    tenant_id = getattr(principal, "tenant_id", None)
    if tenant_id:
        stmt = stmt.where(MemoryItem.tenant_id == tenant_id)
    viewer = viewer_of(principal)
    stmt = stmt.where(or_(MemoryItem.user_id == viewer, MemoryItem.user_id.is_(None)))
    agent_id = getattr(principal, "agent_id", None)
    if agent_id:
        stmt = stmt.where(
            or_(
                MemoryItem.agent_id == agent_id,
                MemoryItem.agent_id.is_(None),
                MemoryItem.shared_scope == "swarm_shared",
            )
        )
    federation_scope = federation_scope_clause(MemoryItem, principal)
    if federation_scope is not None:
        stmt = stmt.where(federation_scope)
    session_id = getattr(principal, "session_id", None)
    if session_id:
        stmt = stmt.where(MemoryItem.session_id == session_id)
    allowed_lanes = getattr(principal, "allowed_lanes", None)
    if allowed_lanes is not None:
        stmt = stmt.where(col(MemoryItem.lane).in_(allowed_lanes))
    return stmt


def _apply_edge_scope(stmt, principal):
    if bool(getattr(principal, "is_admin", False)):
        return stmt
    tenant_id = getattr(principal, "tenant_id", None)
    if tenant_id:
        stmt = stmt.where(MemoryEdge.tenant_id == tenant_id)
    viewer = viewer_of(principal)
    stmt = stmt.where(or_(MemoryEdge.user_id == viewer, MemoryEdge.user_id.is_(None)))
    agent_id = getattr(principal, "agent_id", None)
    if agent_id:
        stmt = stmt.where(or_(MemoryEdge.agent_id == agent_id, MemoryEdge.agent_id.is_(None)))
    return stmt


def _after_cursor(timestamp_column, id_column, rank: int, decoded) -> Any:
    if decoded is None:
        return True
    after_time, after_id, source, _watermark = decoded
    after_rank = {"edge": 0, "memory": 1, "~": 2}[source]
    if rank > after_rank:
        same_time = timestamp_column == after_time
    elif rank < after_rank:
        same_time = False
    else:
        same_time = and_(timestamp_column == after_time, id_column > after_id)
    return or_(timestamp_column > after_time, same_time)


def _apply_filters(stmt, *, scene_id, domain, lane, tag, tags, theme, include_archived):
    if not include_archived:
        stmt = stmt.where(MemoryItem.status == "active")
    if scene_id:
        stmt = stmt.where(MemoryItem.scene_id == scene_id)
    if domain:
        stmt = stmt.where(MemoryItem.domain == domain)
    if lane:
        stmt = stmt.where(MemoryItem.lane == lane)
    requested_tags = [value for value in [tag, *(tags or [])] if value]
    for requested_tag in dict.fromkeys(requested_tags):
        stmt = stmt.where(_json_tag_filter(requested_tag))
    if theme:
        term = theme.strip().lower()
        tags = func.json_each(MemoryItem.tags).table_valued("value").alias("theme_tags")
        stmt = stmt.where(
            or_(
                func.lower(MemoryItem.content).contains(term, autoescape=True),
                func.lower(MemoryItem.key).contains(term, autoescape=True),
                exists(
                    select(1)
                    .select_from(tags)
                    .where(func.lower(tags.c.value).contains(term, autoescape=True))
                ),
            )
        )
    return stmt


def _label_from_text(content: str) -> str:
    content = (content or "").strip().replace("\n", " ")
    if len(content) > LABEL_MAX:
        return content[:LABEL_MAX] + "…"
    return content


def _calculate_node_weight(item: MemoryItem) -> float:
    """计算节点在 3D/力导向图中的物理权重与引力质量。"""
    importance = _clamp_score(item.importance if item.importance is not None else 0.5)
    use_count = max(0, int(item.use_count or 0))
    helpful_count = max(0, int(item.helpful_count or 0))
    # 基础权重 + 对数使用频率增益 + 有用性回馈增益
    return round(1.0 + (importance * 1.5) + math.log1p(use_count) + (0.3 * helpful_count), 3)


def _node_projection(item: MemoryItem) -> dict[str, Any]:
    weight = _calculate_node_weight(item)
    decay = round(_clamp_score(item.decay_score if item.decay_score is not None else 1.0), 3)
    event_time = iso_utc(item.event_time) or None
    valid_from = iso_utc(item.valid_from) or None
    valid_to = iso_utc(item.valid_to) or None
    return {
        "id": item.id,
        "label": _label_from_text(item.content or "") or (item.key or item.id),
        "key": item.key,
        "content": item.content,
        "tags": list(item.tags or []),
        "lane": item.lane,
        "domain": item.domain,
        "tier": item.tier,
        "decay_score": decay,
        "decay_class": item.decay_class,
        "decay_curve": calculate_decay_curve(item.decay_class, decay),
        "weight": weight,
        "importance": round(
            _clamp_score(item.importance if item.importance is not None else 0.5), 3
        ),
        "use_count": int(item.use_count or 0),
        "helpful_count": int(item.helpful_count or 0),
        "scene_id": item.scene_id,
        "shared_scope": getattr(item, "shared_scope", "private"),
        "lifecycle_status": getattr(item, "lifecycle_status", "active"),
        "agent_id": item.agent_id,
        "created_at": iso_utc(item.created_at) or None,
        "updated_at": iso_utc(item.updated_at) or None,
        "event_time": event_time,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "coordinates": {
            "space": {"scene_id": item.scene_id, "lane": item.lane},
            "time": {"event_time": event_time, "valid_from": valid_from, "valid_to": valid_to},
        },
        "halo": calculate_halo(decay, weight),
    }


def build_xuanjing_topology(
    session: Session,
    limit: int = 250,
    scene_id: str | None = None,
    lane: str | None = None,
    include_archived: bool = False,
    principal=None,
    domain: str | None = None,
    tag: str | None = None,
    tags: list[str] | None = None,
    theme: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """构建悬镜记忆星图拓扑数据结构。"""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an int in [1, 1000]")
    decoded = _decode_cursor(cursor)
    stmt = _apply_filters(
        select(MemoryItem),
        scene_id=scene_id,
        domain=domain,
        lane=lane,
        tag=tag,
        tags=tags,
        theme=theme,
        include_archived=include_archived,
    )
    stmt = _apply_scope(stmt, principal)
    watermark = ensure_aware(utcnow())
    if decoded:
        watermark = decoded[3]
    stmt = stmt.where(MemoryItem.updated_at <= watermark)
    if decoded:
        if decoded[2] != "memory":
            raise ValueError("snapshot cursor required")
        after_time, after_id, _source, _watermark = decoded
        stmt = stmt.where(
            or_(
                MemoryItem.updated_at < after_time,
                and_(MemoryItem.updated_at == after_time, MemoryItem.id < after_id),
            )
        )
    stmt = stmt.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc()).limit(limit + 1)
    memories = session.exec(stmt).all()
    has_more = len(memories) > limit
    memories = memories[:limit]
    mem_ids = {m.id for m in memories}
    watermark_iso = iso_utc(watermark)
    next_cursor = (
        _encode_cursor(memories[-1].updated_at, memories[-1].id, watermark=watermark)
        if has_more
        else None
    )

    if not mem_ids:
        # 票 72：两分支 stats 键组必须一致——空图分支同样带 total_*/avg_decay_score
        # （前端 xuanjing.js:89-95 逐字消费这三个键；空图 avg 语义为 None 非 0）
        return {
            "generated_at": _now_iso(),
            "watermark": watermark_iso,
            "stream_cursor": _encode_cursor(watermark, "\uffff", source="~", watermark=watermark),
            "nodes": [],
            "edges": [],
            "clusters": [],
            "stats": {
                "node_count": 0,
                "edge_count": 0,
                "lane_counts": {},
                "relation_counts": {},
                "total_nodes": 0,
                "total_edges": 0,
                "avg_decay_score": None,
            },
            "has_more": False,
            "next_cursor": None,
            "pagination": {"limit": limit, "has_more": False, "next_cursor": None},
        }

    # 查询涉及候选记忆的拓扑边
    edge_stmt = select(MemoryEdge).where(
        or_(
            MemoryEdge.source_memory_id.in_(mem_ids),
            MemoryEdge.target_memory_id.in_(mem_ids),
        )
    )
    raw_edges = session.exec(_apply_edge_scope(edge_stmt, principal)).all()

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
                "provenance": {
                    "reason": e.reason,
                    "user_id": e.user_id,
                    "agent_id": e.agent_id,
                    "session_id": e.session_id,
                },
            }
        )

    # 构筑节点列表
    nodes: list[dict[str, Any]] = []
    for m in memories:
        nodes.append(_node_projection(m))

    # 聚合星系与场景聚簇
    scene_ids = {m.scene_id for m in memories if m.scene_id}
    scenes: dict[str, MemoryScene] = {}
    if scene_ids:
        sc_list = session.exec(select(MemoryScene).where(MemoryScene.id.in_(scene_ids))).all()
        scenes = {sc.id: sc for sc in sc_list}

    scene_members = select(MemoryItem.scene_id, func.count(MemoryItem.id)).where(
        MemoryItem.scene_id.in_(scene_ids or {""})
    )
    all_scene_counts = dict(session.exec(scene_members.group_by(MemoryItem.scene_id)).all())
    visible_scene_counts = dict(
        session.exec(
            _apply_scope(
                scene_members,
                principal,
            ).group_by(MemoryItem.scene_id)
        ).all()
    )
    fully_visible_scenes = {
        scene_id
        for scene_id in scene_ids
        if all_scene_counts.get(scene_id, 0) == visible_scene_counts.get(scene_id, 0)
    }

    # 统计聚簇信息
    cluster_map: dict[str, dict[str, Any]] = {}
    for n in nodes:
        sc_id = n.get("scene_id")
        if sc_id:
            c_key = sc_id
            if c_key not in cluster_map:
                disclose_scene = sc_id in fully_visible_scenes and sc_id in scenes
                sc_name = scenes[sc_id].name if disclose_scene else sc_id
                cluster_map[c_key] = {
                    "id": sc_id,
                    "type": "scene",
                    "name": sc_name,
                    "count": 0,
                    "lanes": set(),
                    "centroid": scenes[sc_id].centroid if disclose_scene else [],
                    "summary": scenes[sc_id].summary if disclose_scene else "",
                    "heat": scenes[sc_id].heat if disclose_scene else 0,
                    "member_count": (
                        scenes[sc_id].member_count
                        if disclose_scene
                        else sum(node.get("scene_id") == sc_id for node in nodes)
                    ),
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
                "centroid": c["centroid"],
                "summary": c["summary"],
                "heat": c["heat"],
                "member_count": c["member_count"],
            }
        )

    lane_map: dict[str, dict[str, Any]] = {}
    for node in nodes:
        lane_name = node["lane"] or "general"
        entry = lane_map.setdefault(
            lane_name,
            {"id": f"lane:{lane_name}", "type": "lane", "name": lane_name, "count": 0},
        )
        entry["count"] += 1
    clusters.extend(lane_map.values())

    return {
        "generated_at": _now_iso(),
        "watermark": watermark_iso,
        "stream_cursor": _encode_cursor(watermark, "\uffff", source="~", watermark=watermark),
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "scenes": [cluster for cluster in clusters if cluster["type"] == "scene"],
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "lane_counts": dict(Counter(n["lane"] for n in nodes)),
            "relation_counts": dict(Counter(e["relation"] for e in edges)),
            # 票 72：前端 xuanjing.js:89-95 消费的三键——total_*/avg_decay_score
            # 此前服务端从不输出，「平均时效」控件恒显 '—'、计数被 ??
            # 掩盖成本次加载量；avg 取节点 decay_score 均值（round 3 位）
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "avg_decay_score": (
                round(sum(n["decay_score"] for n in nodes) / len(nodes), 3) if nodes else None
            ),
        },
        "has_more": has_more,
        "next_cursor": next_cursor,
        "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
    }


def get_xuanjing_graph(
    limit: int = 250,
    scene_id: str | None = None,
    lane: str | None = None,
    include_archived: bool = False,
    principal=None,
    domain: str | None = None,
    tag: str | None = None,
    tags: list[str] | None = None,
    theme: str | None = None,
    cursor: str | None = None,
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
            domain=domain,
            tag=tag,
            tags=tags,
            theme=theme,
            cursor=cursor,
        )


def get_xuanjing_changes(
    session: Session,
    *,
    cursor: str,
    limit: int = 250,
    scene_id: str | None = None,
    domain: str | None = None,
    lane: str | None = None,
    tag: str | None = None,
    theme: str | None = None,
    tags: list[str] | None = None,
    principal=None,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Read a bounded, chronological change page from a snapshot cursor."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an int in [1, 1000]")
    decoded = _decode_cursor(cursor)
    if decoded is None:
        raise ValueError("cursor is required")
    if decoded[2] not in ("~", "memory", "edge"):
        raise ValueError("invalid cursor source")

    window_end = ensure_aware(utcnow()) if decoded[2] == "~" else decoded[3]
    memory_stmt = _apply_scope(select(MemoryItem), principal).where(
        MemoryItem.updated_at <= window_end,
        _after_cursor(MemoryItem.updated_at, MemoryItem.id, 1, decoded),
    )
    changed = session.exec(
        memory_stmt.order_by(MemoryItem.updated_at.asc(), MemoryItem.id.asc()).limit(limit + 1)
    ).all()

    visible_stmt = select(MemoryItem.id)
    visible_stmt = _apply_scope(visible_stmt, principal)
    visible_stmt = _apply_filters(
        visible_stmt,
        scene_id=scene_id,
        domain=domain,
        lane=lane,
        tag=tag,
        tags=tags,
        theme=theme,
        include_archived=include_archived,
    )
    edge_stmt = _apply_edge_scope(select(MemoryEdge), principal).where(
        MemoryEdge.created_at <= window_end,
        _after_cursor(MemoryEdge.created_at, MemoryEdge.id, 0, decoded),
        MemoryEdge.source_memory_id.in_(visible_stmt),
        MemoryEdge.target_memory_id.in_(visible_stmt),
    )
    changed_edges = session.exec(
        edge_stmt.order_by(MemoryEdge.created_at.asc(), MemoryEdge.id.asc()).limit(limit + 1)
    ).all()

    changed_memory_ids = [item.id for item in changed]
    matching_memory_ids = set()
    if changed_memory_ids:
        matching_memory_ids = set(
            session.exec(visible_stmt.where(MemoryItem.id.in_(changed_memory_ids))).all()
        )

    candidates = [
        (ensure_aware(item.updated_at), 1, item.id, "memory", item) for item in changed
    ] + [(ensure_aware(edge.created_at), 0, edge.id, "edge", edge) for edge in changed_edges]
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    has_more = len(candidates) > limit
    candidates = candidates[:limit]
    events: list[dict[str, Any]] = []
    for event_time, _rank, event_id, kind, record in candidates:
        if kind == "memory":
            item = record
            matches = item.id in matching_memory_ids
            events.append(
                {
                    "id": item.id,
                    "at": iso_utc(item.updated_at) or _now_iso(),
                    "type": "memory.upsert" if matches else "memory.remove",
                    "data": _node_projection(item) if matches else {"id": item.id},
                }
            )
        else:
            edge = record
            events.append(
                {
                    "id": edge.id,
                    "at": iso_utc(edge.created_at) or _now_iso(),
                    "type": "edge.created",
                    "data": {
                        "id": edge.id,
                        "source": edge.source_memory_id,
                        "target": edge.target_memory_id,
                        "relation": edge.relation,
                        "confidence": round(float(edge.confidence or 0.0), 3),
                    },
                }
            )

    if has_more and candidates:
        last_time, _rank, last_id, last_source, _record = candidates[-1]
        next_cursor = _encode_cursor(last_time, last_id, source=last_source, watermark=window_end)
    else:
        next_cursor = _encode_cursor(window_end, "\uffff", source="~", watermark=window_end)
    return {
        "generated_at": _now_iso(),
        "cursor": cursor,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "events": events,
        "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
    }
