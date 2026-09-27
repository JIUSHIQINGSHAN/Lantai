"""提示词模板服务（DB 覆盖 `lantai.llm.prompts` 默认值）。

归属（票 .scratch/readside-gaps/11）：模板正文是使用者反复打磨的方法论，
原本 `GET /prompts`、`GET /prompts/{id}` 一个身份都不取、全文可读。
`PromptTemplate` 补了归属四元组（`tables.py`，迁移见
`migrations_v022`），读侧用 `user_id == viewer OR IS NULL` 收窄——
NULL 口径同票 03/04/06/09：老行是单人部署的历史数据，判"不可见"
会让功能直接消失。NULL 是「未记录」不是「属于所有人」。
"""

from sqlmodel import select

from lantai.models.tables import PromptTemplate
from lantai.storage import db


def _prompt_scope(principal):
    """读侧归属条件：admin/`principal=None` → None（不过滤）；
    否则 `user_id == viewer OR user_id IS NULL`。"""
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (PromptTemplate.user_id == viewer) | (PromptTemplate.user_id.is_(None))


def get_prompt(prompt_id: str, default: str, principal=None) -> str:
    """取模板正文；库里没有则回落默认值。

    scope 加在主查询上：按 id 直取这条路径最险——知道 id 就能读到
    别人的模板，不猜 newest。
    """
    with db.get_session() as session:
        q = select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        scope = _prompt_scope(principal)
        if scope is not None:
            q = q.where(scope)
        prompt = session.exec(q).first()
        if prompt:
            return prompt.template
        return default


def update_prompt(prompt_id: str, template: str, description: str = "", principal=None) -> dict:
    """Upsert 一个 PromptTemplate。

    改写**已存在**的行前过 `ensure_can_delete`（A 不能覆写 B 的模板）；
    新建的行落 `principal.user_id`——不造谁都不该看见的行。
    `principal=None` 的内部调用留 NULL，与改动前逐字一致。
    """
    with db.get_session() as session:
        prompt = session.exec(select(PromptTemplate).where(PromptTemplate.id == prompt_id)).first()
        if prompt is not None and principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=prompt.user_id,
                resource_tenant_id=prompt.tenant_id,
            )
        if not prompt:
            prompt = PromptTemplate(
                id=prompt_id,
                template=template,
                description=description,
                user_id=getattr(principal, "user_id", None),
                tenant_id=getattr(principal, "tenant_id", None),
            )
            session.add(prompt)
        else:
            prompt.template = template
            if description:
                prompt.description = description
        session.commit()
        return {"ok": True, "prompt_id": prompt_id}


def list_prompts(principal=None) -> list[dict]:
    """列出模板（按归属收窄）。"""
    with db.get_session() as session:
        q = select(PromptTemplate)
        scope = _prompt_scope(principal)
        if scope is not None:
            q = q.where(scope)
        prompts = session.exec(q).all()
        return [p.model_dump(mode="json") for p in prompts]
