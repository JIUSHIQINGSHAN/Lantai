"""器识（ADR-0029，Persona 人格基座）：REST 路由端点。

归属（票 .scratch/readside-gaps/06）：6 个 handler 原本一个身份都不取。
`PersonaProfile` 的 E 层 `epistemic_facts` 存的是硬件环境、人物关系这类
个人化信息，`/persona/context` 还会把它渲染成 prompt 块——A 一次请求
就能拿到 B 的认知底色，直接喂给自己的 Agent。

读侧走 `persona_service._persona_scope`（`user_id == viewer OR IS NULL`，
NULL 口径见该函数 docstring）；写侧 `set_persona` / `activate_persona`
内部过 `ensure_can_delete`。
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
from lantai.models.schemas import SetPersonaReq
from lantai.services import persona_service

router = APIRouter()


@router.get("/persona")
@router.get("/persona/active")
def get_active(ctx=Depends(get_current_user)):
    """获取当前激活的人格基座（器识 ADR-0029）。"""
    p = persona_service.get_active_persona(principal=ctx)
    if not p:
        return {
            "name": "default",
            "linguistic_style": "",
            "guidelines": "",
            "epistemic_facts": "",
            "is_active": True,
        }
    return p.model_dump(mode="json")


@router.get("/persona/list")
def list_all(ctx=Depends(get_current_user)):
    """列出系统中所有人格基座配置。"""
    personas = persona_service.list_personas(principal=ctx)
    return [p.model_dump(mode="json") for p in personas]


@router.post("/persona")
def create_or_update(req: SetPersonaReq, ctx=Depends(get_current_user)):
    """创建或更新人格基座。"""
    try:
        p = persona_service.set_persona(
            name=req.name,
            linguistic_style=req.linguistic_style,
            guidelines=req.guidelines,
            epistemic_facts=req.epistemic_facts,
            is_active=req.is_active,
            principal=ctx,
        )
        return p.model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/persona/{persona_id}/activate")
def activate(persona_id: str, ctx=Depends(get_current_user)):
    """激活指定的人格基座。"""
    p = persona_service.activate_persona(persona_id, principal=ctx)
    if not p:
        raise HTTPException(status_code=404, detail="Persona not found")
    return p.model_dump(mode="json")


@router.get("/persona/context")
def get_context(ctx=Depends(get_current_user)):
    """获取格式化的人格基座提示词文本块。"""
    return {"context": persona_service.format_persona_context(principal=ctx)}
