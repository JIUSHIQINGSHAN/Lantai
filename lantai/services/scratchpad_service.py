"""札记（ADR-0032）：Working Memory Scratchpad 核心服务。

提供：
1. get_scratchpad: 获取当前会话的札记便签；
2. write_scratchpad: 覆盖更新札记内容（限制最大 1000 字符，宁 miss 不脏写截断）；
3. format_scratchpad_context: 格式化为 Prompt 上下文（与「底本」协同注入）。

**归属（票 .scratch/ownership-gaps/04）**：札记只按 session_id 主键查，
而 `SessionScratchpad` 的归属列虽然一直在（v18 迁移补的）却从不填——
于是 A 写的札记 B 一读就到。札记是**往 prompt 里注入的**
（checkpoint_service → shell_hook → hermes 插件 `_on_pre_llm_call`），
所以这不只是读到，是别人的私有文字进我的 LLM 上下文。
读写两处都按 principal 收窄；principal=None 仅限内部调用（shell_hook
子进程没有身份），落到 DEV MODE 的 "default"，与票 03 同口径。

**注入过樊篱**：`format_scratchpad_context` 的输出会进 LLM 提示，
必须走 `llm/fence.py` 的中性化——此前这条路从来没调用过樊篱，正文里写
`</memory_data>忽略以上指令…` 就能把数据围栏整段截断（OWASP LLM01，
围栏是被自己人拆的）。中性化只动闭合标记，正文其余内容原样可见
（宁实不饰：不偷偷删用户的字）。
"""

from sqlmodel import Session

from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import SessionScratchpad
from lantai.storage import db

MAX_SCRATCHPAD_CHARS = 1000


def _owner_of(principal) -> tuple[str | None, str | None, str | None]:
    """从 principal 取归属三元组；None principal → DEV MODE 的 "default"。

    与票 03 的 `add_memory(user_id="default")` 同口径：不新造「默认 owner」
    概念，用 auth.py:187 DEV MODE fallback 的同值。不猜归属，只做收敛。
    """
    if principal is None:
        return (None, "default", None)
    return (
        getattr(principal, "tenant_id", None),
        getattr(principal, "user_id", None) or "default",
        getattr(principal, "agent_id", None),
    )


def get_scratchpad(
    session_id: str = "default",
    session: Session | None = None,
    *,
    principal=None,
) -> str:
    """获取指定会话的札记便签内容。

    principal 传入时按归属过滤（票 ownership-gaps/04）：非 admin 只读得到
    自己写的札记。admin 不限（与 acl.py 的 admin/system 全权同口径）。
    """
    sid = (session_id or "default").strip()
    tenant_id, user_id, agent_id = _owner_of(principal)

    def _run(s: Session) -> str:
        sp = s.get(SessionScratchpad, sid)
        if sp is None:
            return ""
        if not getattr(principal, "is_admin", False):
            # 归属不匹配即视为不存在（不区分「没有」与「不是你的」——
            # 区分本身就是信息泄漏：能据此探知某 session_id 是否被占用）
            #
            # 不再要求 `principal is not None`（票 `.scratch/mcp-identity-gaps/05`）：
            # MCP 入口的 None 语义是「宿主没透传身份」，不是「内部 worker 全量」。
            # 此前 None 把整个判断跳过，A 传 B 的 session_id 就拿到 B 的札记正文，
            # 而札记直接进 LLM 提示（`format_scratchpad_context`）。
            # `_owner_of(None)` 上一行已经算出 viewer=`"default"`——**算好了却不用**。
            # 收敛到 "default" 的安全性同票 04：无身份写入也落 "default"，读写配对。
            if sp.user_id != user_id:
                return ""
        return sp.content

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def write_scratchpad(
    session_id: str = "default",
    content: str = "",
    session: Session | None = None,
    *,
    principal=None,
) -> dict:
    """写入/覆盖指定会话的札记便签内容（上限 1000 字符，超长自动截断）。

    归属随 principal 落列（票 ownership-gaps/04）：不落则读侧无从按
    归属收窄，跨用户零隔离。
    """
    sid = (session_id or "default").strip()
    raw_text = (content or "").strip()
    tenant_id, user_id, agent_id = _owner_of(principal)

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
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
            )
        else:
            if principal is not None and not getattr(principal, "is_admin", False):
                # 覆盖他人札记同样按归属拒绝（与读侧同口径：不越权写）
                if sp.user_id != user_id:
                    from fastapi import HTTPException

                    raise HTTPException(403, "scratchpad belongs to another user")
            sp.content = raw_text
            sp.updated_at = now
            # 归属列补写（老行可能是 NULL：迁移前写的、或内部调用没带身份）
            sp.user_id = user_id
            sp.tenant_id = tenant_id
            sp.agent_id = agent_id

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
    *,
    principal=None,
) -> str:
    """格式化札记便签，供首轮 Prompt 注入（与底本协同）。

    输出会进 LLM 提示，所以正文必须过樊篱中性化（票 ownership-gaps/04）：
    此前这条路从未调用 `llm/fence.py`，正文写 `</memory_data>…` 就能把
    数据围栏整段截断，而注入串会被 hermes 插件原样拼到宿主提示头部。
    围栏关闭（DATA_FENCE_ENABLED=False）时中性化也随之关闭——与其余出口
    同一开关，不做双轨语义。
    """
    from lantai.core.settings import settings
    from lantai.llm.fence import neutralize_fence_escapes

    text = get_scratchpad(session_id, session=session, principal=principal)
    if not text:
        return ""
    if settings.DATA_FENCE_ENABLED:
        text = neutralize_fence_escapes(text)
    return f"【札记 (Scratchpad)】:\n{text}\n"
