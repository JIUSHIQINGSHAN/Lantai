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

from datetime import UTC, datetime, timedelta

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


_BACKGROUND_LOG_PREFIX = "[IMPORTANT: Background process"


def validate_blocks(blocks: dict) -> list[tuple[str, str]]:
    """过滤非法/过短块，返回 [(key, content)] 合法块（纯函数，可测）。"""
    out: list[tuple[str, str]] = []
    if not isinstance(blocks, dict):
        return out
    for key, label in BLOCK_LABELS.items():  # noqa: B007 label 保留可读性
        c = blocks.get(key)
        if isinstance(c, str) and len(c.strip()) >= settings.CHECKPOINT_MIN_CONTENT:
            if c.lstrip().startswith(_BACKGROUND_LOG_PREFIX):
                continue
            out.append((key, c.strip()[: settings.CHECKPOINT_MAX_CONTENT]))
    return out


def _checkpoint_scope(principal):
    """底本读侧归属条件：admin → None（不过滤）；
    否则 `user_id == viewer OR user_id IS NULL`。

    票 .scratch/readside-gaps/09 口径 3。NULL 口径同票 03/04/06：
    单人部署下老行 `user_id` 为 NULL，判"不可见"会让
    `/checkpoint/latest` 对唯一真实用户返回空，`inject_checkpoint_context`
    拿不到快照 → 下次会话丢失工作现场。NULL 是「未记录」不是「属于所有人」。

    **`principal=None` 收敛到 `"default"`，不再返回 None（不过滤）**
    （票 `.scratch/mcp-identity-gaps/05`）：MCP 入口的 None 语义是
    「宿主没透传身份」，**不是**「内部 worker 要全量」。此前 None 直接
    return None，于是 `get_latest_checkpoint` 一条 where 都不加，
    返回**全库 newest** 的完整五段底本——而它连 `session_id` 参数都不看，
    A 调一次就拿到当前最新的工作现场（很可能是 B 刚写的）。

    收敛到 `"default"` 的安全性由票 04 保证：无身份**写入**现在也落
    `"default"`（`viewer_of(None)`），读写正好配对，不会出现
    「自己写了读不到」。真实库 82 行底本 **100% 是 NULL 属主**，
    靠 `OR IS NULL` 照常可见——对当前部署零影响。

    admin 仍全量（`is_admin` 分支）：运维排查需要看全库 newest。
    """
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    return (SessionCheckpoint.user_id == viewer) | (SessionCheckpoint.user_id.is_(None))


def write_session_checkpoint(session_id: str, blocks: dict, principal=None) -> dict:
    """写入一个会话的五段快照（同 session 替换，upsert 语义）。

    principal 非 None 时把归属落到每一行（票 09 口径 2）——列一直在
    （`tables.py:658`），只是写入方从不填，读侧收窄就没有判据可用。

    归属（票 `.scratch/mcp-identity-gaps/04`）：owner 走 `acl.viewer_of`
    收敛，**不落 NULL**。此前是 `getattr(principal, "user_id", None)`，
    而读侧 `_checkpoint_scope` 的口径是 `user_id == viewer OR user_id IS
    NULL`——NULL 行的可见性是"人人可读"。于是宿主不透传 `user_id` 调 MCP
    `checkpoint_write`（`principal=None`），落一行无主底本，之后**任何**
    用户调 `checkpoint_latest` 都能读到那五段工作现场。

    docstring 原写"`principal=None` 的内部调用留 NULL"——但 grep 全仓只有
    HTTP 路由与 MCP 两个调用方，**没有任何 worker / CLI / 脚本调用者**，
    那条设计对应的场景不存在。同批四个兄弟写工具（`raw_add` /
    `add_dialogue` / `scratchpad_write`）在不透传身份时全部落 `"default"`，
    只有这一个落 NULL。

    tenant / agent 保持 `getattr(..., None)`：它们没有"人人可读"的读侧
    口径，改了只会扩大回归面。`crystal_service.py:84` 的 NULL 是**刻意的**
    （后台巡检确有调用者），不受本票影响。
    """
    if not session_id or len(session_id.strip()) < 3:
        raise ValueError("session_id 至少 3 字符")
    session_id = session_id.strip()
    valid = validate_blocks(blocks)
    now = utcnow()
    from lantai.core.acl import viewer_of

    owner = viewer_of(principal)
    tenant = getattr(principal, "tenant_id", None)
    agent = getattr(principal, "agent_id", None)
    with db.get_session() as s:
        s.exec(delete(SessionCheckpoint).where(SessionCheckpoint.session_id == session_id))
        for key, content in valid:
            s.add(
                SessionCheckpoint(
                    session_id=session_id,
                    block_key=key,
                    content=content,
                    created_at=now,
                    user_id=owner,
                    tenant_id=tenant,
                    agent_id=agent,
                )
            )
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


def get_checkpoint(session_id: str, principal=None) -> dict | None:
    """读取指定会话的快照（无则 None）。

    按 session_id 直读是这条端点最险的地方：不用猜 newest，知道 id
    就能读到那一会话的工作现场。所以 scope 必须加在**主查询**上
    （票 09 口径 3）。
    """
    with db.get_session() as s:
        q = (
            select(SessionCheckpoint)
            .where(SessionCheckpoint.session_id == session_id)
            .order_by(SessionCheckpoint.id)
        )
        scope = _checkpoint_scope(principal)
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q).all()
        return _rows_to_checkpoint(list(rows))


def get_latest_checkpoint(principal=None) -> dict | None:
    """最近一次会话的完整快照（无则 None）。

    scope 加在"挑 newest"那一跳上，不是只加在取行那一跳——否则
    newest 仍是全库的，收窄只发生在后面的取行，A 照样拿到 B 的
    session_id 再按它取全文。
    """
    with db.get_session() as s:
        q = select(SessionCheckpoint).order_by(
            SessionCheckpoint.created_at.desc(), SessionCheckpoint.id.desc()
        )
        scope = _checkpoint_scope(principal)
        if scope is not None:
            q = q.where(scope)
        last = s.exec(q.limit(1)).first()
        if last is None:
            return None
        rows = s.exec(
            select(SessionCheckpoint)
            .where(SessionCheckpoint.session_id == last.session_id)
            .order_by(SessionCheckpoint.id)
        ).all()
        return _rows_to_checkpoint(list(rows))


def cleanup_old_checkpoints(max_sessions: int | None = None, principal=None) -> dict:
    """只保留最近 max_sessions 个会话的快照，删除更早的（ADR-0005 只降权不删——
    快照是记录，保留最近 N 会话即可，删的是超龄会话快照）。

    归属（票 09 口径 5）：这是删除操作。非 admin 只能清**自己作用域内**
    的会话——A 调 cleanup 不该把 B 的会话快照删掉。做法是把候选会话
    集合先用 scope 收窄，保留/淘汰都只在这个集合里算；admin 与
    `principal=None` 的内部调用仍是全库（运维要能清全部）。
    """
    max_sessions = settings.CHECKPOINT_MAX_SESSIONS if max_sessions is None else max_sessions
    if max_sessions < 1:
        raise ValueError("max_sessions must be >= 1")
    scope = _checkpoint_scope(principal)
    with db.get_session() as s:
        # 按会话取最近时间，保留最新 N 个会话 id（sqlmodel 单列 select 返回标量）
        sess_q = select(SessionCheckpoint.session_id).distinct()
        if scope is not None:
            sess_q = sess_q.where(scope)
        sessions = list(s.exec(sess_q).all())
        latest_by: dict[str, tuple[datetime, int]] = {}
        for sid in sessions:
            latest = s.exec(
                select(SessionCheckpoint.created_at, SessionCheckpoint.id)
                .where(SessionCheckpoint.session_id == sid)
                .order_by(SessionCheckpoint.created_at.desc(), SessionCheckpoint.id.desc())
                .limit(1)
            ).first()
            if latest is not None:
                latest_by[sid] = (latest[0], latest[1])
        ordered = sorted(latest_by, key=lambda sid: latest_by[sid], reverse=True)
        keep = ordered[:max_sessions]
        drop = [sid for sid in ordered if sid not in keep]
        deleted = 0
        for sid in drop:
            r = s.exec(delete(SessionCheckpoint).where(SessionCheckpoint.session_id == sid))
            deleted += r.rowcount or 0
        s.commit()
    return {"kept": len(keep), "deleted": deleted, "status": "ok"}


def _parse_naive_utc(value) -> datetime:
    dt = value
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def inject_checkpoint_context(
    now: datetime | None = None,
    include_persona: bool = False,
    include_scratchpad: bool = True,
    session_id: str = "default",
    principal=None,
) -> str:
    """生成注入文本：`[Checkpoint · 上次会话]` + 五段行；陈旧自动标注；可联动注入器识（Persona）与札记（Scratchpad）。

    无快照/无合法块返回空串（零侵入降级）。纯格式函数，测试可注入 now。

    principal 透传给札记（票 ownership-gaps/04）与底本（票 09 口径 6）：
    札记会进 LLM 提示，既要有归属收窄（不读别人的），也要过樊篱
    （正文不能截断 `<memory_data>` 围栏）——后者已由
    format_scratchpad_context 内部做，这里只负责把身份带下去。
    底本同理：`get_latest_checkpoint` 早先不接 principal，
    自动注入路径仍读全库 newest——HTTP 层收窄了，注入路径照样漏。
    """
    parts = []
    if include_persona:
        try:
            from lantai.services.persona_service import format_persona_context

            p_text = format_persona_context(principal=principal)
            if p_text.strip():
                parts.append(p_text.strip())
        except Exception:
            pass

    if include_scratchpad:
        try:
            from lantai.services.scratchpad_service import format_scratchpad_context

            sp_text = format_scratchpad_context(session_id, principal=principal)
            if sp_text.strip():
                parts.append(sp_text.strip())
        except Exception:
            pass

    cp = get_latest_checkpoint(principal=principal)
    if cp and cp.get("blocks"):
        now = now or utcnow()
        stale = False
        created = cp.get("created_at")
        if created:
            try:
                stale = now - _parse_naive_utc(created) > timedelta(
                    days=settings.CHECKPOINT_STALENESS_DAYS
                )
            except (ValueError, TypeError):
                stale = True
        header = "[Checkpoint · 上次会话]"
        if stale:
            header = (
                f"[Checkpoint · 上次会话 ⚠️ {settings.CHECKPOINT_STALENESS_DAYS}天+前，仅供参考]"
            )
        lines = [header]
        for key, label in BLOCK_LABELS.items():
            content = (cp["blocks"] or {}).get(key, "")
            if content.strip():
                lines.append(f"{label}: {content}")

        cp_body = "\n".join(lines) if len(lines) > 1 else ""
        if cp_body.strip():
            parts.append(cp_body.strip())

    return "\n\n".join(parts)
