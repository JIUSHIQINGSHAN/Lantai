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


def ensure_default_persona(session: Session | None = None) -> PersonaProfile:
    """确保系统中至少存在一个默认激活的人格基座（兰台执笔）。"""
    def _run(s: Session) -> PersonaProfile:
        active = s.exec(select(PersonaProfile).where(PersonaProfile.is_active == True)).first()  # noqa: E712
        if active:
            return active

        default_p = s.exec(select(PersonaProfile).where(PersonaProfile.name == DEFAULT_PERSONA_NAME)).first()
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


def get_active_persona(session: Session | None = None) -> PersonaProfile:
    """获取当前激活的人格基座；若无则自动初始化默认人格。"""
    def _run(s: Session) -> PersonaProfile:
        active = s.exec(select(PersonaProfile).where(PersonaProfile.is_active == True)).first()  # noqa: E712
        if active:
            return active
        return ensure_default_persona(s)

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
) -> PersonaProfile:
    """创建或更新人格基座（L/G/E）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("人格名称不可为空")

    # 边界保护（宁 miss 不脏写：截断单字段上限 2000 字符）
    l_style = (linguistic_style or "").strip()[:2000]
    g_lines = (guidelines or "").strip()[:2000]
    e_facts = (epistemic_facts or "").strip()[:2000]

    def _run(s: Session) -> PersonaProfile:
        existing = s.exec(select(PersonaProfile).where(PersonaProfile.name == name)).first()
        now = datetime.now(UTC)

        if is_active:
            # 取消其他所有 active 标记
            all_active = s.exec(select(PersonaProfile).where(PersonaProfile.is_active == True)).all()  # noqa: E712
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


def list_personas(session: Session | None = None) -> list[PersonaProfile]:
    """列出系统内所有人格基座。"""
    def _run(s: Session) -> list[PersonaProfile]:
        # 确保至少有默认项
        ensure_default_persona(s)
        return list(s.exec(
            select(PersonaProfile).order_by(PersonaProfile.is_active.desc(), PersonaProfile.updated_at.desc())
        ).all())

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def activate_persona(persona_id_or_name: str, session: Session | None = None) -> PersonaProfile | None:
    """根据 ID 或名称激活指定人格基座。"""
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


def format_persona_context(persona: PersonaProfile | None = None) -> str:
    """将人格基座格式化为三层提示词注入块。"""
    p = persona or get_active_persona()
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
