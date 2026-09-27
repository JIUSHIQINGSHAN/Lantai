"""器识（ADR-0029，Persona 人格基座）：L/G/E 三层认知模型服务。

提供：
1. 默认人格初始化（兰台执笔）
2. 人格持久化（增/查/激活切换）
3. L/G/E 上下文格式化生成（注入会话首轮）
4. 检索偏好加权辅助
"""

from datetime import UTC, datetime

from sqlmodel import Session, select
from ulid import ULID

from lantai.core.logger import logger
from lantai.models.tables import PersonaProfile
from lantai.storage import db

DEFAULT_PERSONA_NAME = "兰台执笔"
DEFAULT_LINGUISTIC_STYLE = (
    "沉稳典雅，名实相副，言简意赅；思考深邃处可引用古诗词点缀，不堆砌浮华套话。"
)
DEFAULT_GUIDELINES = (
    "遵循「宁 miss 不脏写」原则；核心函数坚持不 mock 真实测试；"
    "操作文件前必须充分核实证据；严格遵守工程质量铁律。"
)
DEFAULT_EPISTEMIC_FACTS = (
    "尊重大哥；开发环境为华硕天选三（RTX 3050，Python 3.12）；本地第一、安全边界明确。"
)


def _persona_scope(principal):
    """画像读侧归属条件：admin 全权；否则 `user_id == viewer OR IS NULL`。

    票 .scratch/readside-gaps/06 修法口径 2：真实库唯一一行 persona 是
    **NULL 属主**（单人部署事实，与 629/650 行 memoryitem 同一来源）。
    判"不可见"会让 `/persona/active` 对唯一真实用户返回空，
    **人格基座直接失效**——那是修废不是收窄。NULL 是「未记录」
    不是「属于所有人」，口径同 `memory_service.get_core_memory`
    （`memory_service.py:391`）与票 03/04。

    `principal=None`（内部 CLI/MCP/worker）不加过滤，与改动前逐字一致。
    """
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (PersonaProfile.user_id == viewer) | (PersonaProfile.user_id.is_(None))


def ensure_default_persona(session: Session | None = None, principal=None) -> PersonaProfile:
    """确保系统中至少存在一个默认激活的人格基座（兰台执笔）。

    两个查询都带 `_persona_scope`（票 06 修法口径 2/3）。这不是洁癖：
    `get_active_persona` 在自己的作用域里找不到 active 行时会回落到这里，
    若这里的 active 查询不带 scope，它会**跨用户捞到 B 的激活画像并
    原样返回**——读侧收窄就被自己的兜底绕过去了（Red 阶段正是这样漏的）。
    第二个查询（按名找默认行）同理：不带 scope 会激活别人的同名行，
    那是借兜底路径写别人的数据。
    """
    scope = _persona_scope(principal)

    def _run(s: Session) -> PersonaProfile:
        q = select(PersonaProfile).where(PersonaProfile.is_active == True)  # noqa: E712
        if scope is not None:
            q = q.where(scope)
        active = s.exec(q).first()
        if active:
            return active

        q2 = select(PersonaProfile).where(PersonaProfile.name == DEFAULT_PERSONA_NAME)
        if scope is not None:
            q2 = q2.where(scope)
        default_p = s.exec(q2).first()
        if default_p:
            default_p.is_active = True
            default_p.updated_at = datetime.now(UTC)
            s.add(default_p)
            s.commit()
            s.refresh(default_p)
            return default_p

        new_p = PersonaProfile(
            id=f"persona_{ULID()}",
            name=DEFAULT_PERSONA_NAME,
            is_active=True,
            linguistic_style=DEFAULT_LINGUISTIC_STYLE,
            guidelines=DEFAULT_GUIDELINES,
            epistemic_facts=DEFAULT_EPISTEMIC_FACTS,
            user_id=getattr(principal, "user_id", None),
            tenant_id=getattr(principal, "tenant_id", None),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        s.add(new_p)
        s.commit()
        s.refresh(new_p)
        logger.info("器识：初始化默认人格基座【%s】", DEFAULT_PERSONA_NAME)
        return new_p

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def get_active_persona(session: Session | None = None, principal=None) -> PersonaProfile:
    """获取当前激活的人格基座；若无则自动初始化默认人格。"""

    def _run(s: Session) -> PersonaProfile:
        q = select(PersonaProfile).where(PersonaProfile.is_active == True)  # noqa: E712
        scope = _persona_scope(principal)
        if scope is not None:
            q = q.where(scope)
        active = s.exec(q).first()
        if active:
            return active
        return ensure_default_persona(s, principal=principal)

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def set_persona(
    name: str,
    linguistic_style: str = "",
    guidelines: str = "",
    epistemic_facts: str = "",
    is_active: bool = True,
    session: Session | None = None,
    principal=None,
) -> PersonaProfile:
    """创建或更新人格基座（L/G/E）。

    写侧归属（票 06 口径 3）：改写已存在的 persona 前过 `ensure_can_delete`
    ——A 不能凭"同名"覆写 B 的三层字段，也不能借停用别人的 active
    行把自己顶上去。新建的行落 principal 的 user_id，不造无主行。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("人格名称不可为空")

    # 边界保护（宁 miss 不脏写：截断单字段上限 2000 字符）
    l_style = (linguistic_style or "").strip()[:2000]
    g_lines = (guidelines or "").strip()[:2000]
    e_facts = (epistemic_facts or "").strip()[:2000]

    def _run(s: Session) -> PersonaProfile:
        existing = s.exec(select(PersonaProfile).where(PersonaProfile.name == name)).first()

        # 归属校验在任何写操作之前：403 不能伴随落库
        # （`PersonaProfile` 没有 lane 列，lane 约束不适用于画像）
        if existing is not None and principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=existing.user_id,
                resource_tenant_id=existing.tenant_id,
            )

        now = datetime.now(UTC)

        if is_active:
            # 取消其他所有 active 标记
            all_active = s.exec(
                select(PersonaProfile).where(PersonaProfile.is_active == True)
            ).all()  # noqa: E712
            for item in all_active:
                if not existing or item.id != existing.id:
                    item.is_active = False
                    item.updated_at = now
                    s.add(item)

        if existing:
            existing.linguistic_style = l_style
            existing.guidelines = g_lines
            existing.epistemic_facts = e_facts
            if is_active:
                existing.is_active = True
            existing.updated_at = now
            s.add(existing)
            s.commit()
            s.refresh(existing)
            return existing

        new_p = PersonaProfile(
            id=f"persona_{ULID()}",
            name=name,
            is_active=is_active,
            linguistic_style=l_style,
            guidelines=g_lines,
            epistemic_facts=e_facts,
            # 新建的行落 principal 的 user_id（`principal=None` 的内部
            # 调用与改动前一致留 NULL，不凭空造"谁都能改"的行）
            user_id=getattr(principal, "user_id", None),
            tenant_id=getattr(principal, "tenant_id", None),
            created_at=now,
            updated_at=now,
        )
        s.add(new_p)
        s.commit()
        s.refresh(new_p)
        return new_p

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def list_personas(session: Session | None = None, principal=None) -> list[PersonaProfile]:
    """列出系统内所有人格基座（按归属收窄）。"""

    def _run(s: Session) -> list[PersonaProfile]:
        # 确保至少有默认项（带 principal：兜底不能越过读侧作用域
        # 去激活别人的同名默认行）
        ensure_default_persona(s, principal=principal)
        q = select(PersonaProfile).order_by(
            PersonaProfile.is_active.desc(), PersonaProfile.updated_at.desc()
        )
        scope = _persona_scope(principal)
        if scope is not None:
            q = q.where(scope)
        return list(s.exec(q).all())

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def activate_persona(
    persona_id_or_name: str, session: Session | None = None, principal=None
) -> PersonaProfile | None:
    """根据 ID 或名称激活指定人格基座。

    写侧归属（票 06 口径 3）：activate 是破坏性操作——它会停掉**其他所有**
    active 行。A 激活 B 的 persona 等于替 B 决定该用哪套认知底色，
    所以先过 `ensure_can_delete`（403 不能伴随落库）。

    单例语义下"激活自己时顺带停掉别人的 active"不算越权：那是
    `is_active` 全局单例的必然，不是越权访问。
    """
    target = (persona_id_or_name or "").strip()
    if not target:
        return None

    def _run(s: Session) -> PersonaProfile | None:
        item = s.exec(
            select(PersonaProfile).where(
                (PersonaProfile.id == target) | (PersonaProfile.name == target)
            )
        ).first()
        if not item:
            return None

        # 归属校验在任何写操作之前
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            ensure_can_delete(
                principal,
                resource_user_id=item.user_id,
                resource_tenant_id=item.tenant_id,
            )

        now = datetime.now(UTC)
        all_active = s.exec(select(PersonaProfile).where(PersonaProfile.is_active == True)).all()  # noqa: E712
        for act in all_active:
            if act.id != item.id:
                act.is_active = False
                act.updated_at = now
                s.add(act)

        item.is_active = True
        item.updated_at = now
        s.add(item)
        s.commit()
        s.refresh(item)
        return item

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def format_persona_context(persona: PersonaProfile | None = None, principal=None) -> str:
    """将人格基座格式化为三层提示词注入块。"""
    p = persona or get_active_persona(principal=principal)
    if not p:
        return ""

    parts = [f"=== 【器识·人格基座（{p.name}）】 ==="]
    if p.linguistic_style:
        parts.append(f"【言语风格 (L)】: {p.linguistic_style}")
    if p.guidelines:
        parts.append(f"【行为准则 (G)】: {p.guidelines}")
    if p.epistemic_facts:
        parts.append(f"【认知底色 (E)】: {p.epistemic_facts}")
    parts.append("===============================")
    return "\n".join(parts)
