"""记忆关系图路由（v0.9 MAP 星图数据服务，只读）。

借鉴 aiduMEI v18.3.0 MAP 面板数据服务（/knowledge/tree）的只读窄版：
兰台用自家 MemoryEdge + scene 归属，不引入作者的四类节点概念。
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lantai.core.auth import Principal, get_current_user
from lantai.ops.graph import get_graph, validate_graph_limit
from lantai.services.xuanjing_service import get_xuanjing_changes, get_xuanjing_graph
from lantai.storage import db

router = APIRouter()


class DecayPointResponse(BaseModel):
    days: int
    score: float


class HaloResponse(BaseModel):
    radius: float
    intensity: float
    opacity: float


class NodeCoordinatesResponse(BaseModel):
    space: dict[str, str | None]
    time: dict[str, str | None]


class XuanjingNodeResponse(BaseModel):
    id: str
    label: str
    key: str | None = None
    content: str = ""
    tags: list[Any] = Field(default_factory=list)
    lane: str | None = None
    domain: str | None = None
    tier: str | None = None
    decay_score: float
    decay_class: str
    decay_curve: list[DecayPointResponse]
    weight: float
    importance: float
    use_count: int
    helpful_count: int
    scene_id: str | None = None
    shared_scope: str = "private"
    lifecycle_status: str = "active"
    agent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    event_time: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    coordinates: NodeCoordinatesResponse
    halo: HaloResponse


class EdgeProvenanceResponse(BaseModel):
    reason: str = ""
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


class XuanjingEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    confidence: float
    provenance: EdgeProvenanceResponse = Field(default_factory=EdgeProvenanceResponse)


class XuanjingClusterResponse(BaseModel):
    id: str
    type: Literal["scene", "lane"]
    name: str
    count: int
    lanes: list[str] = Field(default_factory=list)
    centroid: list[Any] = Field(default_factory=list)
    summary: str = ""
    heat: float = 0
    member_count: int = 0


class XuanjingStatsResponse(BaseModel):
    node_count: int
    edge_count: int
    lane_counts: dict[str, int]
    relation_counts: dict[str, int]
    total_nodes: int
    total_edges: int
    avg_decay_score: float | None


class MemoryUpsertEventResponse(BaseModel):
    id: str
    at: str
    type: Literal["memory.upsert"]
    data: XuanjingNodeResponse


class MemoryRemoveEventResponse(BaseModel):
    id: str
    at: str
    type: Literal["memory.remove"]
    data: dict[Literal["id"], str]


class EdgeCreatedDataResponse(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    confidence: float


class EdgeCreatedEventResponse(BaseModel):
    id: str
    at: str
    type: Literal["edge.created"]
    data: EdgeCreatedDataResponse


XuanjingChangeEventResponse = Annotated[
    MemoryUpsertEventResponse | MemoryRemoveEventResponse | EdgeCreatedEventResponse,
    Field(discriminator="type"),
]


class PaginationResponse(BaseModel):
    limit: int
    has_more: bool
    next_cursor: str | None


class XuanjingGraphResponse(BaseModel):
    """Validated top-level contract for the read-only 3D observatory payload."""

    generated_at: str
    watermark: str = ""
    stream_cursor: str = ""
    nodes: list[XuanjingNodeResponse] = Field(default_factory=list)
    edges: list[XuanjingEdgeResponse] = Field(default_factory=list)
    clusters: list[XuanjingClusterResponse] = Field(default_factory=list)
    scenes: list[XuanjingClusterResponse] = Field(default_factory=list)
    stats: XuanjingStatsResponse
    has_more: bool = False
    next_cursor: str | None = None
    pagination: PaginationResponse


class XuanjingChangesResponse(BaseModel):
    """Validated bounded change-feed contract for incremental clients."""

    generated_at: str
    cursor: str
    next_cursor: str | None = None
    has_more: bool = False
    events: list[XuanjingChangeEventResponse] = Field(default_factory=list)
    pagination: PaginationResponse


@router.get("/graph")
def graph_route(limit: int = 150, principal: Principal = Depends(get_current_user)) -> dict:
    """记忆关系星图（只读）：节点 + MemoryEdge 链接 + lane/relation 统计。"""
    try:
        validate_graph_limit(limit)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return get_graph(limit, principal=principal)


@router.get("/graph/xuanjing", response_model=XuanjingGraphResponse)
def xuanjing_graph_route(
    limit: int = Query(250, ge=1, le=1000, description="每页最大节点数"),
    scene_id: str | None = Query(None, description="限定场景 ID"),
    domain: str | None = Query(None, description="限定辨域"),
    lane: str | None = Query(None, description="限定泳道"),
    tag: str | None = Query(None, description="精确标签"),
    tags: Annotated[list[str] | None, Query()] = None,
    theme: str | None = Query(None, description="主题子串"),
    cursor: str | None = Query(None, max_length=4096, description="快照分页游标"),
    include_archived: bool = Query(False, description="是否包含已归档记忆"),
    principal: Principal = Depends(get_current_user),
) -> XuanjingGraphResponse:
    """悬镜记忆星图快照，按 ACL 筛选并使用 keyset 分页。"""
    try:
        return get_xuanjing_graph(
            limit=limit,
            scene_id=scene_id,
            domain=domain,
            lane=lane,
            tag=tag,
            tags=tags,
            theme=theme,
            cursor=cursor,
            include_archived=include_archived,
            principal=principal,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/graph/xuanjing/changes", response_model=XuanjingChangesResponse)
def xuanjing_changes_route(
    cursor: str = Query(..., min_length=1, max_length=4096, description="快照或增量续读游标"),
    limit: int = Query(250, ge=1, le=1000, description="每页最大事件数"),
    scene_id: str | None = Query(None, description="限定场景 ID"),
    domain: str | None = Query(None, description="限定辨域"),
    lane: str | None = Query(None, description="限定泳道"),
    tag: str | None = Query(None, description="精确标签"),
    tags: Annotated[list[str] | None, Query()] = None,
    theme: str | None = Query(None, description="主题子串"),
    include_archived: bool = Query(False, description="是否包含已归档记忆"),
    principal: Principal = Depends(get_current_user),
) -> XuanjingChangesResponse:
    """从快照水位读取有界增量事件页。"""
    try:
        with db.get_session() as session:
            return get_xuanjing_changes(
                session,
                cursor=cursor,
                limit=limit,
                scene_id=scene_id,
                domain=domain,
                lane=lane,
                tag=tag,
                tags=tags,
                theme=theme,
                include_archived=include_archived,
                principal=principal,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
