"""法门（Famen）REST 路由端点 (ADR-0067)。

对外暴露任务法门动态匹配、法门列表与详细 SOP 步骤获取。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from lantai.core.auth import get_current_user
from lantai.services.famen_service import (
    get_skill_detail_service,
    list_skills_service,
    match_skills_service,
)

router = APIRouter(tags=["famen"])


@router.get("/famen/match")
def famen_match_route(
    task: str = Query(..., description="待执行的目标任务描述"),
    limit: int = Query(5, ge=1, le=20, description="最大返回法门数量"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """根据任务意图动态匹配适用的法门技能与 SOP。"""
    matched = match_skills_service(task=task, limit=limit, principal=ctx)
    return {"task": task, "count": len(matched), "skills": matched}


@router.get("/famen/skills/{skill_id}")
def famen_skill_detail_route(
    skill_id: str,
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """获取指定法门的详细 SOP 步骤（带樊篱防注入保护）。"""
    skill = get_skill_detail_service(skill_id=skill_id, principal=ctx)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found or access denied")
    return skill


@router.get("/famen/list")
def famen_list_route(
    limit: int = Query(50, ge=1, le=200, description="分页大小"),
    ctx=Depends(get_current_user),
) -> dict[str, Any]:
    """列出当前租户权限范围内的所有可用法门资产。"""
    skills = list_skills_service(limit=limit, principal=ctx)
    return {"count": len(skills), "items": skills}
