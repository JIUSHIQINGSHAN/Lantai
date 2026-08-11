"""技能结晶路由（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）。"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import CrystalDecideReq
from lantai.services import crystal_service

router = APIRouter()


@router.get("/crystals")
def crystals_list_route(status: str = "candidate", limit: int = 50):
    """结晶候选项列表（默认 candidate 待审）。"""
    return crystal_service.list_crystals(status, limit)


@router.post("/crystals/detect")
def crystals_detect_route(dry_run: bool = False):
    """执行一轮结晶检测：聚类 -> 候选（dry_run=true 不写库）。"""
    return crystal_service.run_crystal_detect_once(dry_run=dry_run)


@router.post("/crystals/{crystal_id}/decide")
def crystal_decide_route(crystal_id: str, req: CrystalDecideReq):
    """裁决候选：approve 必须带非空 steps -> 落 Skill 资产；reject -> archived。"""
    try:
        return crystal_service.decide_crystal(
            crystal_id, req.approve, req.steps, req.reason)
    except ValueError as e:
        raise HTTPException(422, str(e))
