"""底本（session checkpoint，ADR-0021）：五段会话快照服务。

五段块（借鉴 aiduMEM checkpoint.py 窄版，v11 Hyperion「5 段会话快照」）：
- cp_active_intent   在做（当前意图）
- cp_next_action     下一步（下一步动作）
- cp_current_work    工作区（当前工作现场）
- cp_key_decisions   决策（关键决策）
- cp_open_notes      待办（未竟事项）

语义：上下文压缩时写入（write_session_checkpoint），下次会话启动时注入
（inject_checkpoint_context）；陈旧（> CHECKPOINT_STALENESS_DAYS）注入自动标注。
宁 miss 不脏写：块内容 < CHECKPOINT_MIN_CONTENT 不落、非法 block_key 拒绝、
session_id < 3 字符拒绝；同 session 重写即替换（upsert）。
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import delete, select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import SessionCheckpoint
from lantai.storage import db

BLOCK_LABELS: dict[str, str] = {
    "cp_active_intent": "在做",
    "cp_next_action": "下一步",
    "cp_current_work": "工作区",
    "cp_key_decisions": "决策",
    "cp_open_notes": "待办",
}


def validate_blocks(blocks: dict) -> list[tuple[str, str]]:
    """过滤非法/过短块，返回 [(key, content)] 合法块（纯函数，可测）。"""
    out: list[tuple[str, str]] = []
    if not isinstance(blocks, dict):
        return out
    for key, label in BLOCK_LABELS.items():  # noqa: B007 label 保留可读性
        c = blocks.get(key)
        if isinstance(c, str) and len(c.strip()) >= settings.CHECKPOINT_MIN_CONTENT:
            out.append((key, c.strip()[:settings.CHECKPOINT_MAX_CONTENT]))
    return out


def write_session_checkpoint(session_id: str, blocks: dict) -> dict:
    """写入一个会话的五段快照（同 session 替换，upsert 语义）。"""
    if not session_id or len(session_id.strip()) < 3:
        raise ValueError("session_id 至少 3 字符")
    session_id = session_id.strip()
    valid = validate_blocks(blocks)
    now = utcnow()
    with db.get_session() as s:
        s.exec(delete(SessionCheckpoint)
               .where(SessionCheckpoint.session_id == session_id))
        for key, content in valid:
            s.add(SessionCheckpoint(
                session_id=session_id, block_key=key,
                content=content, created_at=now))
        s.commit()
    return {"session_id": session_id, "blocks_written": len(valid), "status": "ok"}


def _rows_to_checkpoint(rows: list[SessionCheckpoint]) -> dict | None:
    if not rows:
        return None
    blocks = {r.block_key: r.content for r in rows}
    return {
        "session_id": rows[0].session_id,
        "blocks": blocks,
        "created_at": rows[0].created_at,
    }


def get_checkpoint(session_id: str) -> dict | None:
    """读取指定会话的快照（无则 None）。"""
    with db.get_session() as s:
        rows = s.exec(select(SessionCheckpoint)
                      .where(SessionCheckpoint.session_id == session_id)
                      .order_by(SessionCheckpoint.id)).all()
        return _rows_to_checkpoint(list(rows))


def get_latest_checkpoint() -> dict | None:
    """最近一次会话的完整快照（无则 None）。"""
    with db.get_session() as s:
        last = s.exec(select(SessionCheckpoint)
                      .order_by(SessionCheckpoint.created_at.desc(),
                                SessionCheckpoint.id.desc())
                      .limit(1)).first()
        if last is None:
            return None
        rows = s.exec(select(SessionCheckpoint)
                      .where(SessionCheckpoint.session_id == last.session_id)
                      .order_by(SessionCheckpoint.id)).all()
        return _rows_to_checkpoint(list(rows))


def cleanup_old_checkpoints(max_sessions: int | None = None) -> dict:
    """只保留最近 max_sessions 个会话的快照，删除更早的（ADR-0005 只降权不删——
    快照是记录，保留最近 N 会话即可，删的是超龄会话快照）。"""
    max_sessions = (settings.CHECKPOINT_MAX_SESSIONS
                    if max_sessions is None else max_sessions)
    if max_sessions < 1:
        raise ValueError("max_sessions must be >= 1")
    with db.get_session() as s:
        # 按会话取最近时间，保留最新 N 个会话 id（sqlmodel 单列 select 返回标量）
        sessions = list(s.exec(select(SessionCheckpoint.session_id).distinct()).all())
        latest_by: dict[str, datetime] = {}
        for sid in sessions:
            m = s.exec(select(SessionCheckpoint.created_at)
                       .where(SessionCheckpoint.session_id == sid)
                       .order_by(SessionCheckpoint.created_at.desc())
                       .limit(1)).first()
            if m is not None:
                latest_by[sid] = m
        ordered = sorted(latest_by, key=lambda sid: latest_by[sid], reverse=True)
        keep = ordered[:max_sessions]
        drop = [sid for sid in ordered if sid not in keep]
        deleted = 0
        for sid in drop:
            r = s.exec(delete(SessionCheckpoint)
                       .where(SessionCheckpoint.session_id == sid))
            deleted += r.rowcount or 0
        s.commit()
    return {"kept": len(keep), "deleted": deleted, "status": "ok"}


def _parse_naive_utc(value) -> datetime:
    dt = value
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def inject_checkpoint_context(now: datetime | None = None) -> str:
    """生成注入文本：`[Checkpoint · 上次会话]` + 五段行；陈旧自动标注。

    无快照/无合法块返回空串（零侵入降级）。纯格式函数，测试可注入 now。
    """
    cp = get_latest_checkpoint()
    if not cp or not cp.get("blocks"):
        return ""
    now = now or utcnow()
    stale = False
    created = cp.get("created_at")
    if created:
        try:
            stale = (now - _parse_naive_utc(created)
                     > timedelta(days=settings.CHECKPOINT_STALENESS_DAYS))
        except (ValueError, TypeError):
            stale = True
    header = "[Checkpoint · 上次会话]"
    if stale:
        header = (f"[Checkpoint · 上次会话 ⚠️ {settings.CHECKPOINT_STALENESS_DAYS}"
                  f"天+前，仅供参考]")
    lines = [header]
    for key, label in BLOCK_LABELS.items():
        content = (cp["blocks"] or {}).get(key, "")
        if content.strip():
            lines.append(f"{label}: {content}")
    return "\n".join(lines) if len(lines) > 1 else ""
