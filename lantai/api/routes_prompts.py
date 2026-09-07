from fastapi import APIRouter
from pydantic import BaseModel

from lantai.llm import prompts as default_prompts
from lantai.services.prompt_service import get_prompt, list_prompts, update_prompt

router = APIRouter(tags=["prompts"])


class PromptUpdateReq(BaseModel):
    template: str
    description: str = ""


@router.get("/prompts")
def get_all_prompts_route():
    return list_prompts()


@router.get("/prompts/{prompt_id}")
def get_prompt_route(prompt_id: str):
    # Try to find a default from lantai.llm.prompts by checking uppercase attributes
    default_val = getattr(default_prompts, prompt_id.upper(), "")
    if not default_val:
        # Sometimes keys are not uppercase identically, e.g., EXTRACT_SYS -> extract_sys
        # Or we just fallback to empty string
        default_val = getattr(default_prompts, prompt_id, "")

    template = get_prompt(prompt_id, default_val)
    return {"id": prompt_id, "template": template}


@router.put("/prompts/{prompt_id}")
def update_prompt_route(prompt_id: str, req: PromptUpdateReq):
    return update_prompt(prompt_id, req.template, req.description)
