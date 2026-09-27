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


def _visible_conflict_ids(s, principal) -> set[str] | None:
    """当前 principal 可见的冲突 id 集合；None 表示 admin 全权（不过滤）。

    口径同 `work_item_service._owner_scope`：admin/system 全权；
    否则只认 `user_id == viewer`，**NULL 属主不可见**
    （宁可漏列，不可错放——`incoming_ref` 是正文级字段）。

    **孤儿冲突（`memory_id` 指向已删除的记忆）对非 admin 不可见**：
    账本没有归属列，归属只能从挂载记忆过渡推导；记忆没了就推导不出属主，
    于是无法证明它"不是别人的"。这正是 `_owner_scope` 的 NULL 口径——
    NULL 不等于 default，宁可漏列。代价是孤儿账本从列表里消失；
    但 admin 仍看得见、仍可裁决，不会永久卡死。

    **`principal=None` 不是"不过滤"，是收敛到 `"default"`**——
    同 `_viewer_of` 的既有约定（`work_item_service.py:503-517`）。
    所以这里只对 admin 提前返回 None。
    """
    if principal is not None and bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    rows = s.exec(
        select(ConflictEvent.id, MemoryItem.user_id).join(
            MemoryItem, ConflictEvent.memory_id == MemoryItem.id, isouter=True
        )
    ).all()
    return {cid for cid, uid in rows if uid is not None and uid == viewer}


def list_conflict_events(limit: int = 50, status: str = "open", principal=None) -> dict:
    """冲突账本列表（默认 open），按创建时间倒序。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 3）：见模块 docstring。
    `principal=None`（内部 CLI/MCP/worker）**收敛到 `"default"`**
    ——同 `_viewer_of` 的既有约定，不是完全不过滤。
    """
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {_ALLOWED_STATUSES}")
    with db.get_session() as s:
        visible = _visible_conflict_ids(s, principal)
        q = select(ConflictEvent).order_by(ConflictEvent.created_at.desc())
        if status != "all":
            q = q.where(ConflictEvent.status == status)
        rows = s.exec(q).all()
        if visible is not None:
            rows = [r for r in rows if r.id in visible]
        return {"events": [r.model_dump(mode="json") for r in rows[:limit]]}


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
