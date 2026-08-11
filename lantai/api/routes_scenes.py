"""场景聚合路由（ADR-0012）——薄路由，业务全在 scene_service。

POST /scenes/rebuild    幂等全量重建（embedding 聚类 + LLM 命名可降级）
POST /scenes/assign     增量聚类补跑（无 scene_id 的 active 记忆并入既有场景）
GET  /scenes            场景列表（heat 降序）
GET  /scenes/{scene_id} 场景 + 成员详情（MCP scene_get 下钻同源）
"""
from fastapi import APIRouter, HTTPException

from lantai.services import scene_service

router = APIRouter(prefix="/scenes", tags=["scenes"])


@router.post("/rebuild")
def rebuild_scenes(threshold: float | None = None) -> dict:
    return scene_service.rebuild_scenes(threshold=threshold)


@router.post("/assign")
def assign_unassigned(limit: int = 50, threshold: float | None = None) -> dict:
    """增量聚类补跑：无 scene_id 的 active 记忆并入既有场景（消化期自动调用同源）。"""
    return scene_service.assign_unassigned(limit=limit, threshold=threshold)

@router.get("")
def list_scenes(limit: int = 50) -> dict:
    try:
        return scene_service.list_scenes(limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{scene_id}")
def get_scene(scene_id: str) -> dict:
    try:
        return scene_service.get_scene(scene_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
