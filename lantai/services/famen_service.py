"""法门（Famen）服务层——程序性技能资产与 SOP 执行流水线服务 (ADR-0067)。

为 REST 路由、MCP 工具与认知上下文切面提供程序性技能检索与详情服务。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.famen import FamenEngine
from lantai.core.auth import Principal
from lantai.storage import db


def match_skills_service(
    *,
    task: str,
    limit: int = 5,
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """根据任务意图动态匹配推荐适用的法门技能。"""
    with db.get_session() as session:
        engine = FamenEngine(session=session, principal=principal)
        return engine.match_skills(task=task, limit=limit)


def get_skill_detail_service(
    *,
    skill_id: str,
    principal: Principal | None = None,
) -> dict[str, Any] | None:
    """获取指定法门的详细 SOP 步骤及元数据（带樊篱防注入保护）。"""
    with db.get_session() as session:
        engine = FamenEngine(session=session, principal=principal)
        return engine.get_skill_detail(skill_id=skill_id)


def list_skills_service(
    *,
    limit: int = 50,
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """获取当前租户权限范围内的法门列表。"""
    with db.get_session() as session:
        engine = FamenEngine(session=session, principal=principal)
        return engine.list_skills(limit=limit)
