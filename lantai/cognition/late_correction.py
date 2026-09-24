"""迟到更正闭环（更漏 ADR-0048 决策 4 / 票 10，spec §4.2 流程）。

三型分流：替换型（本模块 supersede）/ 自始错误型（导流笔削 retract，ADR-0047，
不设 valid_to——曾为真与从未为真语义分离）/ 失效型（仅回写 valid_to）。

替换型四步（一个事务内，spec §4.2）：
  a. MemoryEdge(new → old, "supersedes")
  b. old.lifecycle_status=SUPERSEDED, superseded_by=new.id, superseded_at=utcnow()
     （事务轴如实：判定发生在今天）
  c. old.valid_to = 锚(new)，new.valid_from = 锚(new)（事件轴如实：现实失效点）
     —— I3 钳制豁免：锚点早于 old.valid_from（回填 created_at 所致）时钳制至
     valid_from，detail 记 clamped/anchor_raw（拒绝会让最常见迟到更正无法落库）
  d. MemoryCheckpoint 快照留底
全程 ConflictEvent(kind="override") 落账 + 直断留痕（人工闸门不绕：本函数只做
「胜出执行」，胜出裁决由 ConflictEngine/人工在调用前完成）。
"""

from datetime import datetime

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.evolution.promoter import _make_checkpoint
from lantai.models.tables import ConflictEvent, LifecycleStatus, MemoryEdge, MemoryItem


def _ensure_utc(dt: datetime) -> datetime:
    """I5：naive 按 UTC 解释（沿 _chronos_filter/ConflictEngine 现行纪律）。"""
    from datetime import timezone

    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def correction_anchor(new_item: MemoryItem) -> datetime:
    """锚(new) = new.event_time ?? new.valid_from；两者皆无时用 new.created_at（approximated）。"""
    if new_item.event_time:
        return _ensure_utc(new_item.event_time)
    if new_item.valid_from:
        return _ensure_utc(new_item.valid_from)
    return new_item.created_at or utcnow()


def apply_supersede_correction(
    old_item: MemoryItem,
    new_item: MemoryItem,
    *,
    session=None,
    reason: str = "",
    actor: str = "",
    correction_type: str = "supersede",
) -> dict:
    """替换型迟到更正（spec §4.2 四步 + I3 钳制豁免）。

    session 传入时复用调用方事务（同事务原子性）；否则自开。
    返回 {ok, old_id, new_id, valid_to_anchor, clamped, approximated}。
    """
    own_session = session is None
    s = session or db_get_session()
    try:
        anchor = correction_anchor(new_item)
        approximated = new_item.event_time is None

        # c. 事件轴锚定 + I3 钳制豁免（spec §2.5 I3 的迟到更正例外）
        clamped = False
        anchor_raw = None
        old_vf = _ensure_utc(old_item.valid_from) if old_item.valid_from else None
        if old_vf is not None and anchor < old_vf:
            clamped = True
            anchor_raw = anchor
            anchor = old_vf  # 钳制：宁近似不静默（账本留原始锚点）

        # a. supersedes 边（同事务直写；create_edge 开独立 session 会破坏原子性）
        edge = MemoryEdge(
            id=new_id("edge"),
            source_memory_id=new_item.id,
            target_memory_id=old_item.id,
            relation="supersedes",
            confidence=0.9,
        )
        s.add(edge)

        # b. 知命转移（事务轴如实：判定发生在今天）
        old_item.lifecycle_status = LifecycleStatus.SUPERSEDED
        old_item.superseded_by = new_item.id
        old_item.superseded_at = utcnow()

        # c. 事件轴回写（替换型：旧值现实失效点 = 新值现实起点）
        old_item.valid_to = anchor
        new_item.valid_from = anchor

        # d. checkpoint 快照留底（旧值）
        _make_checkpoint(s, old_item, {}, actor or reason or "late_correction", trigger="late_correction")

        # 落账：ConflictEvent(kind="override") detail 契约（spec §4.2 步骤 1/2）
        s.add(
            ConflictEvent(
                id=new_id("cfev"),
                memory_id=old_item.id,
                incoming_ref=new_item.content[:200],
                kind="override",
                detail={
                    "correction_type": correction_type,
                    "old_event_time": old_item.event_time.isoformat() if old_item.event_time else None,
                    "new_event_time": new_item.event_time.isoformat() if new_item.event_time else None,
                    "valid_to_anchor": anchor.isoformat() if anchor else None,
                    "recency_axis": "event" if new_item.event_time else "created",
                    "approximated": approximated,
                    "clamped": clamped,
                    "anchor_raw": anchor_raw.isoformat() if anchor_raw else None,
                    "reason": reason,
                    "actor": actor,
                },
                status="resolved",
            )
        )
        s.add_all([old_item, new_item])
        s.flush()  # 同事务落写（commit 时机归调用方；避免 refresh 读回旧值）
        if own_session:
            s.commit()
        return {
            "ok": True,
            "old_id": old_item.id,
            "new_id": new_item.id,
            "valid_to_anchor": anchor.isoformat() if anchor else None,
            "clamped": clamped,
            "approximated": approximated,
        }
    except Exception as exc:  # noqa: BLE001 —— 事务失败如实上抛给调用方处置
        if own_session:
            s.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        if own_session:
            s.close()


def apply_expire_correction(
    old_item: MemoryItem,
    *,
    valid_to: datetime,
    reason: str = "",
    actor: str = "",
    session=None,
) -> dict:
    """失效型：事实自然到期，无新值、无 supersedes 边——仅回写 valid_to（I3 校验：违例拒写）。"""
    own_session = session is None
    s = session or db_get_session()
    try:
        vt_norm = _ensure_utc(valid_to)
        vf_norm = _ensure_utc(old_item.valid_from) if old_item.valid_from else None
        if vf_norm is not None and vt_norm < vf_norm:
            return {"ok": False, "error": "I3: valid_to < valid_from"}
        old_item.valid_to = vt_norm
        _make_checkpoint(s, old_item, {}, actor or reason or "expire", trigger="late_correction")
        s.add(
            ConflictEvent(
                id=new_id("cfev"),
                memory_id=old_item.id,
                incoming_ref=reason[:200],
                kind="override",
                detail={
                    "correction_type": "expire",
                    "valid_to_anchor": valid_to.isoformat(),
                    "approximated": False,
                    "clamped": False,
                    "actor": actor,
                },
                status="resolved",
            )
        )
        s.add(old_item)
        s.flush()  # 同事务落写（commit 时机归调用方）
        if own_session:
            s.commit()
        return {"ok": True, "old_id": old_item.id, "valid_to": vt_norm.isoformat()}
    except Exception as exc:  # noqa: BLE001
        if own_session:
            s.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        if own_session:
            s.close()


def db_get_session():
    from lantai.storage import db

    return db.get_session()
