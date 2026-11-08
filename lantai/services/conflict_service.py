"""冲突账本 service 层（P0-2）：list / resolve ConflictEvent。

账本由 gate/decision.py 在闸门决策时写入（确定性规则命中）；此处提供审计
与人工裁决入口（REST + MCP），裁决不影响闸门结果，只标记处置。

归属（票 .scratch/readside-gaps/07 修法口径 3）：
- `list_conflict_events` 此前全表捞，把别人的 `incoming_ref` 正文连同
  `memory_id` 一起吐出来（`OBSERVED3.txt` 实证）。
- `resolve_conflict_event` 此前无校验，实测 A 能把 B 的冲突
  `open → dismissed`——那是替 B 做判断。

`ConflictEvent` **没有归属列**（`tables.py:371-383`），判据过渡推导：
`conf.memory_id → MemoryItem.user_id`。账本的存在意义就是挂在某条记忆上，
记忆换属主、冲突可见性跟着换，比加列更准。
"""

from sqlmodel import select

from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.storage import db

_ALLOWED_STATUSES = ("open", "resolved", "dismissed")


def _conflict_owner_user_id(s, event_id: str) -> str | None:
    """冲突的属主：过渡到它挂载的那条记忆（账本本身没有归属列）。"""
    ev = s.get(ConflictEvent, event_id)
    if not ev:
        return None
    item = s.get(MemoryItem, ev.memory_id)
    return item.user_id if item else None


def list_conflict_events(limit: int = 50, status: str = "open", principal=None) -> dict:
    """冲突账本列表（默认 open），按创建时间倒序。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 3）：见模块 docstring。
    `principal=None`（内部 CLI/MCP/worker）**收敛到 `"default"`**
    ——同 `_viewer_of` 的既有约定，不是完全不过滤。

    票 42 两处收口：
    1. **limit 下推 SQL**——修复前全表 `.all()` 取回后 `rows[:limit]` Python
       切片，负 limit 是「去掉末尾 N 条」的语义倒挂（与 SQL 负 limit 完全不同），
       且全表捞本身是性能债；现在 join/where/order/limit 全在 SQL 里。
    2. **可见性判定一并下推**——非 admin 改走 inner join 挂载记忆 +
       `user_id == viewer`：孤儿冲突（join 不上）与 NULL 属主（NULL != viewer）
       天然不可见，与原 Python 预过滤（本函数旧实现取全表后按可见 id 集合
       筛选再切片）的「宁可漏列」口径逐条一致；admin 不 join 全权，
       `principal=None` 经 `_viewer_of` 收敛 `"default"`。
       行为锁定见 `tests/test_rest_param_bounds.py` 对照臂。
    """
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {_ALLOWED_STATUSES}")
    with db.get_session() as s:
        q = select(ConflictEvent).order_by(ConflictEvent.created_at.desc())
        if status != "all":
            q = q.where(ConflictEvent.status == status)
        if not (principal is not None and bool(getattr(principal, "is_admin", False))):
            # 非 admin:归属判定下推 join(inner join 天然排除孤儿冲突与 NULL 属主)
            from lantai.services.work_item_service import _viewer_of

            q = q.join(MemoryItem, ConflictEvent.memory_id == MemoryItem.id).where(
                MemoryItem.user_id == _viewer_of(principal)
            )
        # admin/system 全权:不加 join,孤儿与无主行照见(运维/裁决需要)
        rows = s.exec(q.limit(limit)).all()
        return {"events": [r.model_dump(mode="json") for r in rows]}


def resolve_conflict_event(event_id: str, decision: str, note: str = "", principal=None) -> dict:
    """人工裁决冲突事件：decision ∈ resolved（确认冲突成立）/ dismissed（误报）。

    归属校验（票 .scratch/readside-gaps/07 修法口径 3）：裁决是**破坏性操作**
    （状态机不可逆，已裁决的冲突再裁会报 "not open"），复用
    `acl.ensure_can_delete` 单一真源（同票 02 口径）：admin/system 全权；
    非 admin 只能裁决自己记忆上的冲突。403 发生在任何 `s.commit()` 之前。

    `principal=None`（内部 CLI/MCP）不加校验，与改动前逐字一致。
    """
    if decision not in ("resolved", "dismissed"):
        raise ValueError("decision must be 'resolved' or 'dismissed'")
    if not (note or "").strip():
        raise ValueError("decision reason is required")
    with db.get_session() as s:
        ev = s.get(ConflictEvent, event_id)
        if not ev:
            raise ValueError("conflict event not found")
        if ev.status != "open":
            raise ValueError(f"conflict event not open (status={ev.status})")

        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            item = s.get(MemoryItem, ev.memory_id)
            ensure_can_delete(
                principal,
                resource_user_id=item.user_id if item else None,
                resource_tenant_id=item.tenant_id if item else None,
                lane=item.lane if item else None,
            )

        ev.status = decision
        ev.resolved_by = note.strip()[:200]
        ev.resolved_at = utcnow()
        s.add(ev)
        s.commit()
        return {"ok": True, "event_id": ev.id, "status": ev.status}


def audit_consistency_service(
    memory_ids: list[str] | None = None,
    entity_name: str | None = None,
    limit: int = 50,
    principal=None,
) -> dict:
    """参商（ADR-0062）：信念一致性审计矩阵服务。"""
    from lantai.cognition.canshang import CanshangEngine

    engine = CanshangEngine(principal=principal)
    report = engine.audit_consistency(memory_ids=memory_ids, entity_name=entity_name, limit=limit)

    return {
        "total_scanned": report.total_scanned,
        "violation_count": report.violation_count,
        "violations": [
            {
                "conflicting_memory_ids": v.conflicting_memory_ids,
                "violation_type": v.violation_type,
                "severity": v.severity,
                "description": v.description,
                "metadata": v.metadata,
            }
            for v in report.violations
        ],
        "audited_at": report.audited_at.isoformat(),
        "entity_name": report.entity_name,
    }


def arbitrate_deep_service(
    conflicting_ids: list[str],
    principal=None,
) -> dict:
    """参商（ADR-0062）：深层多方冲突仲裁直断服务。"""
    from lantai.cognition.canshang import CanshangEngine

    engine = CanshangEngine(principal=principal)
    result = engine.arbitrate_deep_conflict(conflicting_ids=conflicting_ids)

    return {
        "dominant_memory_id": result.dominant_memory_id,
        "subordinate_memory_ids": result.subordinate_memory_ids,
        "resolution_strategy": result.resolution_strategy,
        "decision_trace": result.decision_trace,
        "narrative": result.narrative,
    }
