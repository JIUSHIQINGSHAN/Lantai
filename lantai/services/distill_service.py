"""咀华（Juhua，会话精华萃取，v022 吸收票据 04，借鉴上游 aiduMEI v21.2 session distill）。

一个会话结束时，把「这一程最值得记住的事」提炼成一两句话，单独存一条。

**为什么不是「再归纳一次」**：普通写入把每轮拆成若干条语义事实，颗粒是
「事实」；精华的颗粒是「这一程」。两者不可互相替代——所以精华单独成条、
单独一个泳道（`distill`，慢衰减），而不是给某条已有记忆加个标记。

**LLM 不可用时不许静默不产出**：超时/返回空/异常，一律退到确定性降级——
取该会话最长的两条原文拼接（长度是**可复现**的代理指标；随便挑一条或挑
最新一条都会让降级产出随机漂移），metadata 标 `distill_mode=fallback`，
不冒充提炼。

**落库走完整管线**：精华必须过闸门管线（add_memory → candidate → evolve
gate → proposal）才进得了向量库、才召回得到；本服务只负责提炼，落库复用
现有 add_memory（票据纪律，同上游「照反思那样落独立表等于没做」）。
"""

from sqlmodel import select

from lantai.core.auth import Principal
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.llm.client import chat_json
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 情绪词表（有界、可读、可回溯）：精华的初始显著性按命中数有界加成，
# 每个数字都能回溯到这张词表，不是拍脑袋的分数（上游同款纪律）。
EMOTION_WORDS: tuple = (
    "开心",
    "高兴",
    "快乐",
    "满意",
    "喜欢",
    "兴奋",
    "感动",
    "惊喜",
    "安心",
    "难过",
    "伤心",
    "生气",
    "愤怒",
    "烦躁",
    "焦虑",
    "担心",
    "害怕",
    "沮丧",
    "失望",
    "后悔",
    "讨厌",
    "郁闷",
    "委屈",
    "尴尬",
    "困惑",
    "震惊",
    "自豪",
    "感激",
    "感谢",
    "抱歉",
    "愧疚",
    "纠结",
    "犹豫",
    "激动",
    "紧张",
    "放松",
)

DISTILL_SYSTEM = (
    "你在为一个长期记忆系统提炼会话精华。只输出 JSON："
    '{"summary": "一到两句话"}。要求：具体，点出人和事；'
    "不要罗列，不要总结套话；不要超过 80 字。"
)


def _emotion_hits(text: str) -> int:
    """数情绪词命中数（词表见 EMOTION_WORDS，纯函数零 LLM）。"""
    if not text:
        return 0
    return sum(1 for kw in EMOTION_WORDS if kw in text)


def collect_session_memories(session_id: str, principal: "Principal | None" = None) -> list[dict]:
    """取这个会话写进来的记忆原文（按 MemoryItem.session_id，票据 01 来源链）。

    只认显式 session_id——那是写入方透传进来的、唯一能把「这一程」圈出来
    的键；读不到返回空，不去猜时间窗（按时间圈会把并发的别的会话卷进来）。

    权限收窄（整改票 03）：非 admin 调用者只读其泳道集内的源记忆（与检索
    出口 filter_results_by_lanes 同口径，不引入 user_id 过滤）；泳道集为空
    即无可读源（宁 miss 不越权）。principal=None 仅限内部调用（脚本/调度）。"""
    if not (session_id or "").strip():
        return []
    stmt = select(MemoryItem).where(
        MemoryItem.session_id == session_id.strip(),
        MemoryItem.status == "active",
    )
    if principal is not None and not principal.is_admin:
        stmt = stmt.where(MemoryItem.lane.in_(principal.allowed_lanes or []))
    with db.get_session() as s:
        rows = s.exec(
            stmt.order_by(MemoryItem.created_at.asc()).limit(int(settings.DISTILL_MAX_SOURCE))
        ).all()
    return [{"id": m.id, "content": m.content, "created_at": m.created_at} for m in rows]


def _fallback_summary(rows: list[dict]) -> str:
    """LLM 不可用时的确定性降级：取最长的两条原文拼接。

    长度是可复现的代理指标——降级就该老实承认自己是降级（metadata 里标
    fallback），产出可复现，不冒充提炼。"""
    best = sorted(rows, key=lambda r: len(r["content"]), reverse=True)[:2]
    parts = [r["content"].strip().replace("\n", " ")[:120] for r in best if r["content"].strip()]
    return "；".join(parts)


def _llm_summary(joined: str) -> str:
    """LLM 提炼；返回空串表示不可用（异常在内部消化，不阻断降级路径）。"""
    try:
        data = chat_json(DISTILL_SYSTEM, joined)
        summary = str((data or {}).get("summary") or "").strip()
        return summary[:400]
    except Exception as exc:  # 网络层/解析层/未配置——统一视为不可用
        logger.info("咀华：LLM 提炼不可用，走确定性降级: %s", type(exc).__name__)
        return ""


def distill_session(
    session_id: str, *, store: bool = False, principal: Principal | None = None
) -> dict:
    """提炼一个会话的精华；store=True 时经 add_memory 走完整闸门管线落库。

    principal 用于源记忆读取的泳道收窄（写侧授权在路由层，整改票 03）。
    返回结构化结果（含未产出的原因）——「这次怎么没精华」必须可查。"""
    if not settings.DISTILL_ENABLED:
        return {"status": "skipped", "reason": "disabled", "session_id": session_id}
    rows = collect_session_memories(session_id, principal=principal)
    if len(rows) < int(settings.DISTILL_MIN_MEMORIES):
        # 不是故障：短会话本来就没什么可提炼的，但要把原因说出来
        return {
            "status": "skipped",
            "reason": "too_short",
            "session_id": session_id,
            "source_count": len(rows),
            "min_required": int(settings.DISTILL_MIN_MEMORIES),
        }

    joined = "\n".join(f"- {r['content']}" for r in rows)
    emo = _emotion_hits(joined)

    summary = _llm_summary(joined)
    mode = "llm" if summary else "fallback"
    if not summary:
        summary = _fallback_summary(rows)
    if not summary:
        return {
            "status": "skipped",
            "reason": "empty_after_fallback",
            "session_id": session_id,
            "source_count": len(rows),
        }

    # 情感成分给初始显著性一个**有界**加成：0 命中 0.60，命中越多越高，
    # 上限 0.85。不设 1.0——精华已走慢衰减泳道，再给满分等于永不遗忘。
    initial = round(min(0.60 + 0.05 * emo, 0.85), 4)

    result = {
        "status": "ok",
        "session_id": session_id,
        "summary": summary,
        "mode": mode,
        "source_count": len(rows),
        "emotion_hits": emo,
        "initial_salience": initial,
    }
    if store:
        from lantai.models.schemas import AddMemoryReq
        from lantai.services.memory_service import add_memory

        result["store"] = add_memory(
            AddMemoryReq(
                source_type="session_distill",
                title=f"会话精华 {utcnow().date().isoformat()}",
                content=summary,
                lane="distill",
                session_id=session_id,
                metadata={
                    "kind": "session_distill",
                    "distill_mode": mode,
                    "distill_source_count": len(rows),
                    "distill_emotion_hits": emo,
                    "distill_initial_salience": initial,
                    "origin_agent": "session-distill",
                },
            )
        )
    return result
