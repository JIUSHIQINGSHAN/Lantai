"""提示词模板路由（DB 覆盖 `lantai.llm.prompts` 默认值）。

归属（票 .scratch/readside-gaps/11）：模板正文是使用者反复打磨的方法论，
`GET /prompts`、`GET /prompts/{id}` 原本一个身份都不取、全文可读。
`PUT /prompts/{id}` 改写已存在的行前过 `ensure_can_delete`
（A 不能覆写 B 的模板）。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.llm import prompts as default_prompts
from lantai.services.prompt_service import get_prompt, list_prompts, update_prompt

router = APIRouter(tags=["prompts"])


class PromptUpdateReq(BaseModel):
    template: str
    description: str = ""


@router.get("/prompts")
def get_all_prompts_route(ctx=Depends(get_current_user)):
    return list_prompts(principal=ctx)


@router.get("/prompts/{prompt_id}")
def get_prompt_route(prompt_id: str, ctx=Depends(get_current_user)):
    # Try to find a default from lantai.llm.prompts by checking uppercase attributes
    default_val = getattr(default_prompts, prompt_id.upper(), "")
    if not default_val:
        # Sometimes keys are not uppercase identically, e.g., EXTRACT_SYS -> extract_sys
        # Or we just fallback to empty string
        default_val = getattr(default_prompts, prompt_id, "")

    template = get_prompt(prompt_id, default_val, principal=ctx)
    return {"id": prompt_id, "template": template}


@router.put("/prompts/{prompt_id}")
def update_prompt_route(prompt_id: str, req: PromptUpdateReq, ctx=Depends(get_current_user)):
    return update_prompt(prompt_id, req.template, req.description, principal=ctx)
