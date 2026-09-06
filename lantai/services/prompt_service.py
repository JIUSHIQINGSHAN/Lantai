from sqlmodel import select

from lantai.models.tables import PromptTemplate
from lantai.storage import db


def get_prompt(prompt_id: str, default: str) -> str:
    """Fetch prompt from DB or return the default fallback."""
    with db.get_session() as session:
        prompt = session.get(PromptTemplate, prompt_id)
        if prompt:
            return prompt.template
        return default


def update_prompt(prompt_id: str, template: str, description: str = "") -> dict:
    """Upsert a PromptTemplate into the DB."""
    with db.get_session() as session:
        prompt = session.get(PromptTemplate, prompt_id)
        if not prompt:
            prompt = PromptTemplate(id=prompt_id, template=template, description=description)
            session.add(prompt)
        else:
            prompt.template = template
            if description:
                prompt.description = description
        session.commit()
        return {"ok": True, "prompt_id": prompt_id}


def list_prompts() -> list[dict]:
    with db.get_session() as session:
        prompts = session.exec(select(PromptTemplate)).all()
        return [p.model_dump(mode="json") for p in prompts]
