"""卷宗（Dossier）REST 路由 (ADR-0063)。

提供长程任务情境卷宗的创建、列表、详情、里程碑推进、记忆绑定与情境聚焦端点。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import Principal, get_current_user
from lantai.models.schemas import (
    DossierBindReq,
    DossierCloseReq,
    DossierCreateReq,
    DossierFocusReq,
    DossierMilestoneUpdateReq,
)
from lantai.services import dossier_service

router = APIRouter(tags=["dossiers"])


@router.post("/dossiers")
def create_dossier_route(
    req: DossierCreateReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """创建长程任务情境卷宗。"""
    try:
        return dossier_service.create_dossier_service(
            title=req.title,
            goal=req.goal,
            milestones=req.milestones,
            memory_ids=req.memory_ids,
            metadata=req.metadata,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/dossiers")
def list_dossiers_route(
    status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """查询卷宗列表。"""
    return dossier_service.list_dossiers_service(
        status=status,
        limit=limit,
        principal=principal,
    )


@router.get("/dossiers/{dossier_id}")
def get_dossier_route(
    dossier_id: str,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """获取指定卷宗详情。"""
    dossier = dossier_service.get_dossier_service(dossier_id, principal=principal)
    if not dossier:
        raise HTTPException(status_code=404, detail=f"Dossier '{dossier_id}' not found")
    return dossier


@router.post("/dossiers/{dossier_id}/bind")
def bind_dossier_memories_route(
    dossier_id: str,
    req: DossierBindReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """绑定记忆项至卷宗。"""
    try:
        return dossier_service.bind_memories_service(
            dossier_id=dossier_id,
            memory_ids=req.memory_ids,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dossiers/{dossier_id}/unbind")
def unbind_dossier_memories_route(
    dossier_id: str,
    req: DossierBindReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """从卷宗解绑记忆项。"""
    try:
        return dossier_service.unbind_memories_service(
            dossier_id=dossier_id,
            memory_ids=req.memory_ids,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dossiers/{dossier_id}/milestones")
def update_dossier_milestone_route(
    dossier_id: str,
    req: DossierMilestoneUpdateReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """更新卷宗任务里程碑状态。"""
    try:
        return dossier_service.update_milestone_service(
            dossier_id=dossier_id,
            milestone_id=req.milestone_id,
            status=req.status,
            notes=req.notes,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dossiers/{dossier_id}/focus")
def focus_dossier_context_route(
    dossier_id: str,
    req: DossierFocusReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """针对指定卷宗执行情境记忆聚焦检索。"""
    try:
        return dossier_service.focus_dossier_service(
            dossier_id=dossier_id,
            query=req.query,
            limit=req.limit,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dossiers/{dossier_id}/close")
def close_dossier_route(
    dossier_id: str,
    req: DossierCloseReq,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    """办结或归档卷宗。"""
    try:
        return dossier_service.close_dossier_service(
            dossier_id=dossier_id,
            summary=req.summary,
            archived=req.archived,
            principal=principal,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
