"""器识（ADR-0029，Persona 人格基座）：REST 路由端点。"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import SetPersonaReq
from lantai.services import persona_service

router = APIRouter()


@router.get("/persona")
@router.get("/persona/active")
def get_active():
    """获取当前激活的人格基座（器识 ADR-0029）。"""
    p = persona_service.get_active_persona()
    if not p:
        return {"name": "default", "linguistic_style": "", "guidelines": "", "epistemic_facts": "", "is_active": True}
    return p.model_dump(mode="json")



@router.get("/persona/list")
def list_all():
    """列出系统中所有人格基座配置。"""
    personas = persona_service.list_personas()
    return [p.model_dump(mode="json") for p in personas]


@router.post("/persona")
def create_or_update(req: SetPersonaReq):
    """创建或更新人格基座。"""
    try:
        p = persona_service.set_persona(
            name=req.name,
            linguistic_style=req.linguistic_style,
            guidelines=req.guidelines,
            epistemic_facts=req.epistemic_facts,
            is_active=req.is_active,
        )
        return p.model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/persona/{persona_id}/activate")
def activate(persona_id: str):
    """激活指定的人格基座。"""
    p = persona_service.activate_persona(persona_id)
    if not p:
        raise HTTPException(status_code=404, detail="Persona not found")
    return p.model_dump(mode="json")


@router.get("/persona/context")
def get_context():
    """获取格式化的人格基座提示词文本块。"""
    return {"context": persona_service.format_persona_context()}
