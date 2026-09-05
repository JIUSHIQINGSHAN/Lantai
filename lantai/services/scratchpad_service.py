"""札记（ADR-0032）：Working Memory Scratchpad 核心服务。

提供：
1. get_scratchpad: 获取当前会话的札记便签；
2. write_scratchpad: 覆盖更新札记内容（限制最大 1000 字符，宁 miss 不脏写截断）；
3. format_scratchpad_context: 格式化为 Prompt 上下文（与「底本」协同注入）。
"""

from sqlmodel import Session

from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import SessionScratchpad
from lantai.storage import db

MAX_SCRATCHPAD_CHARS = 1000


def get_scratchpad(session_id: str = "default", session: Session | None = None) -> str:
    """获取指定会话的札记便签内容。"""
    sid = (session_id or "default").strip()

    def _run(s: Session) -> str:
        sp = s.get(SessionScratchpad, sid)
        return sp.content if sp else ""

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def write_scratchpad(
    session_id: str = "default",
    content: str = "",
    session: Session | None = None,
) -> dict:
    """写入/覆盖指定会话的札记便签内容（上限 1000 字符，超长自动截断）。"""
    sid = (session_id or "default").strip()
    raw_text = (content or "").strip()

    # 1000 字符截断防护（宁 miss 不脏写）
    if len(raw_text) > MAX_SCRATCHPAD_CHARS:
        logger.warning(
            "札记：内容超出上限（%d > %d），自动执行安全截断",
            len(raw_text),
            MAX_SCRATCHPAD_CHARS,
        )
        raw_text = raw_text[:MAX_SCRATCHPAD_CHARS]

    def _run(s: Session) -> dict:
        sp = s.get(SessionScratchpad, sid)
        now = utcnow()
        if not sp:
            sp = SessionScratchpad(
                session_id=sid,
                content=raw_text,
                created_at=now,
                updated_at=now,
            )
        else:
            sp.content = raw_text
            sp.updated_at = now

        s.add(sp)
        s.commit()
        s.refresh(sp)
        logger.info("札记：会话【%s】已更新便签（len=%d）", sid, len(sp.content))
        return sp.model_dump(mode="json")

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def format_scratchpad_context(
    session_id: str = "default",
    session: Session | None = None,
) -> str:
    """格式化札记便签，供首轮 Prompt 注入（与底本协同）。"""
    text = get_scratchpad(session_id, session=session)
    if not text:
        return ""
    return f"【札记 (Scratchpad)】:\n{text}\n"
