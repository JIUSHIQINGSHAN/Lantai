"""卷宗业务服务层 (ADR-0063 卷宗)

封装 JuanzongEngine 核心认知能力，对接 REST 路由与 MCP 适配器，
向下透传 Principal 租户安全上下文，执行数据字典序列化与状态流转。
"""

from __future__ import annotations

from typing import Any

from lantai.cognition.juanzong import JuanzongEngine
from lantai.core.auth import Principal
from lantai.models.tables import Dossier


def _serialize_dossier(dossier: Dossier) -> dict[str, Any]:
    return {
        "id": dossier.id,
        "tenant_id": dossier.tenant_id,
        "user_id": dossier.user_id,
        "title": dossier.title,
        "goal": dossier.goal,
        "status": dossier.status,
        "milestones": list(dossier.milestones or []),
        "memory_ids": list(dossier.memory_ids or []),
        "metadata": dict(dossier.metadata_json or {}),
        "created_at": dossier.created_at.isoformat() if dossier.created_at else None,
        "updated_at": dossier.updated_at.isoformat() if dossier.updated_at else None,
    }


def create_dossier_service(
    title: str,
    goal: str = "",
    milestones: list[dict[str, Any]] | None = None,
    memory_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """创建长程任务卷宗服务。"""
    engine = JuanzongEngine(principal=principal)
    dossier = engine.create_dossier(
        title=title,
        goal=goal,
        milestones=milestones,
        memory_ids=memory_ids,
        metadata=metadata,
    )
    return _serialize_dossier(dossier)


def get_dossier_service(
    dossier_id: str,
    principal: Principal | None = None,
) -> dict[str, Any] | None:
    """获取指定卷宗详情（受租户行级隔离保护）。"""
    engine = JuanzongEngine(principal=principal)
    dossier = engine.get_dossier(dossier_id)
    if not dossier:
        return None
    return _serialize_dossier(dossier)


def list_dossiers_service(
    status: str | None = None,
    limit: int = 50,
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """查询当前用户/租户的卷宗列表。"""
    engine = JuanzongEngine(principal=principal)
    items = engine.list_dossiers(status=status, limit=limit)
    return [_serialize_dossier(it) for it in items]


def bind_memories_service(
    dossier_id: str,
    memory_ids: list[str],
    principal: Principal | None = None,
) -> dict[str, Any]:
    """向卷宗绑定记忆项。"""
    engine = JuanzongEngine(principal=principal)
    ok = engine.bind_memories(dossier_id, memory_ids)
    if not ok:
        raise ValueError(f"Dossier '{dossier_id}' not found or access denied")
    dossier = engine.get_dossier(dossier_id)
    return {
        "ok": True,
        "dossier_id": dossier_id,
        "memory_ids": list(dossier.memory_ids or []) if dossier else [],
    }


def unbind_memories_service(
    dossier_id: str,
    memory_ids: list[str],
    principal: Principal | None = None,
) -> dict[str, Any]:
    """从卷宗解绑记忆项。"""
    engine = JuanzongEngine(principal=principal)
    ok = engine.unbind_memories(dossier_id, memory_ids)
    if not ok:
        raise ValueError(f"Dossier '{dossier_id}' not found or access denied")
    dossier = engine.get_dossier(dossier_id)
    return {
        "ok": True,
        "dossier_id": dossier_id,
        "memory_ids": list(dossier.memory_ids or []) if dossier else [],
    }


def update_milestone_service(
    dossier_id: str,
    milestone_id: str,
    status: str,
    notes: str | None = None,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """流转更新任务里程碑。"""
    engine = JuanzongEngine(principal=principal)
    dossier = engine.update_milestone(
        dossier_id=dossier_id,
        milestone_id=milestone_id,
        status=status,
        notes=notes,
    )
    if not dossier:
        raise ValueError(f"Dossier '{dossier_id}' not found or access denied")
    return _serialize_dossier(dossier)


def focus_dossier_service(
    dossier_id: str,
    query: str | None = None,
    limit: int = 20,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """执行卷宗情境记忆聚焦检索。"""
    engine = JuanzongEngine(principal=principal)
    dossier = engine.get_dossier(dossier_id)
    if not dossier:
        raise ValueError(f"Dossier '{dossier_id}' not found or access denied")
    focus_res = engine.focus_dossier_context(
        dossier_id=dossier_id,
        query=query,
        limit=limit,
    )
    return {
        "dossier_id": focus_res.dossier_id,
        "dossier_title": focus_res.dossier_title,
        "dossier_goal": focus_res.dossier_goal,
        "focused_memories": focus_res.focused_memories,
        "milestones": focus_res.milestones,
        "total_bound_memories": focus_res.total_bound_memories,
        "focused_at": focus_res.focused_at.isoformat(),
    }


def close_dossier_service(
    dossier_id: str,
    summary: str | None = None,
    archived: bool = False,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """办结或封藏卷宗。"""
    engine = JuanzongEngine(principal=principal)
    dossier = engine.close_dossier(
        dossier_id=dossier_id,
        summary=summary,
        archived=archived,
    )
    if not dossier:
        raise ValueError(f"Dossier '{dossier_id}' not found or access denied")
    return _serialize_dossier(dossier)
