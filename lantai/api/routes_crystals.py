"""技能结晶路由（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）。

归属（票 .scratch/readside-gaps/11）：`GET /crystals` 原本一个身份都不取，
`procedure` / `trigger_rule`（从记忆里蒸馏出的操作流程）全文可读。
`POST /crystals/{id}/decide` 是裁决操作，同票 02 口径走
`ensure_can_delete`——A 不能替 B 批准一个技能结晶。
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
from lantai.evolution.crystal_promoter import promote_crystal
from lantai.models.schemas import CrystalDecideReq, CrystalPromoteReq
from lantai.services import crystal_service

router = APIRouter()


@router.get("/crystals")
def crystals_list_route(status: str = "candidate", limit: int = 50, ctx=Depends(get_current_user)):
    """结晶候选项列表（默认 candidate 待审）。"""
    return crystal_service.list_crystals(status, limit, principal=ctx)


@router.post("/crystals/detect")
def crystals_detect_route(dry_run: bool = False, ctx=Depends(get_current_user)):
    """执行一轮结晶检测：聚类 -> 候选（dry_run=true 不写库）。"""
    return crystal_service.run_crystal_detect_once(dry_run=dry_run, principal=ctx)


@router.post("/crystals/{crystal_id}/decide")
def crystal_decide_route(crystal_id: str, req: CrystalDecideReq, ctx=Depends(get_current_user)):
    """裁决候选：approve 必须带非空 steps -> 落 Skill 资产；reject -> archived。"""
    try:
        return crystal_service.decide_crystal(
            crystal_id, req.approve, req.steps, req.reason, principal=ctx
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/crystals/{crystal_id}/promote")
def crystal_promote_route(crystal_id: str, req: CrystalPromoteReq, ctx=Depends(get_current_user)):
    """同袍（ADR-0058）：将技能结晶晋升发布至公共知识殿堂（swarm_shared）。"""
    try:
        return promote_crystal(
            crystal_id,
            steps=req.steps,
            min_success_count=req.min_success_count,
            principal=ctx,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
