"""对话写通道（Ticket 01）——让记忆从对话中自动长出来

复用现有 rawdocument → memorycandidate 链，不新建存储、
不改 fastpath / gate 语义：

1. fastpath 白名单直通（"记住：X" / 自我声明 / 偏好表达）——绕过 LLM 提取
2. 闲聊（过短 / 纯社交结束语）——直接 rejected（沙汰，ADR-0026，不进待审队列）
3. 其余文本——LLM 提取建候选（status=new，走现有 gate 分层）；
   低置信度 / 提取失败（上游 502）→ 候选进待审队列，不丢数据
"""

import hashlib
import logging
import re
from datetime import timedelta

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.provenance import (
    PROVENANCE_PROMPT_DIALOGUE_CHITCHAT,
    PROVENANCE_PROMPT_DIALOGUE_FASTPATH,
    PROVENANCE_PROMPT_DIALOGUE_IMPORT,
    PROVENANCE_PROMPT_EXTRACT,
    make_provenance,
)
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.gate.prefilter import NO_MEMORY_PATTERNS
from lantai.models.tables import MemoryCandidate, RawDocument
from lantai.parsing.extractor import extract_candidate
from lantai.parsing.fastpath import fastpath_check
from lantai.services.candidate_service import enqueue_rejected
from lantai.storage import db

logger = logging.getLogger("lantai.ingestion")


class DialogueDisabledError(ValueError):
    """对话通道被 `DIALOGUE_ENABLED=false` 整体关闭（票 43：接线死开关）。

    继承 ValueError：MCP 分发器与导入的逐条计数（宁 miss 不脏写）经既有
    异常面自动获得；REST 侧在路由层按更具体类型译 409（服务端配置拒绝，
    非调用方输入问题，不得落 422 滥用口径）。
    """


def assert_dialogue_enabled() -> None:
    """通道开关单一真源：四个入口（REST/MCP/潜移/导入）统一经此 guard，
    禁止各入口散写判据（票 43）。关闭时留痕后拒绝——防「静默不跑」新病灶
    （口径同票 34 跳过留痕）。"""
    if settings.DIALOGUE_ENABLED:
        return
    logger.warning("对话摄取被 DIALOGUE_ENABLED=false 阻断（通道整体关闭，票 43）")
    raise DialogueDisabledError("dialogue channel disabled (DIALOGUE_ENABLED=false)")

# 对话入口的 lane 预判（宽松 search；fastpath 整段 match 语义保持不变）
_PREFERENCE_RE = re.compile(r"我.{0,8}?(?:喜欢|不喜欢|讨厌|偏爱|偏好)")
_FACT_RE = re.compile(r"^我是|^我叫|我的(?:名字|生日|年龄|地址|电话|邮箱)|我住在|我来自")


def _guess_lane(text: str) -> str:
    """对话文本 lane 启发式：偏好 / 事实 / 默认 general。"""
    if _PREFERENCE_RE.search(text):
        return "preference"
    if _FACT_RE.search(text):
        return "fact"
    return "general"


def _is_chitchat(text: str) -> bool:
    """闲聊判断：过短或纯社交结束语 → 不进记忆库。"""
    t = text.strip()
    if len(t) < settings.DIALOGUE_MIN_CHARS:
        return True
    return bool(NO_MEMORY_PATTERNS.match(t))


def ingest_dialogue(
    text: str,
    *,
    user_id: str = "default",
    source: str = "dialogue",
    created_at=None,
    session_id: str = "",
    turn: int | None = None,
) -> dict:
    """对话文本 → 现有提取链。

    created_at：冷启动导入时传原始消息时间戳（naive UTC）——RawDocument/
    MemoryCandidate 用该时间，provenance.prompt=dialogue-session-import，
    随演化链继承到 MemoryItem（时间线不压平）。

    session_id / turn：来源链显式透传（v022 票据 01，上游 aiduMEI v21.2
    教训：隐式通道靠不住，出身必须由写入方显式落列）——落
    MemoryCandidate.session_id 列与 provenance.origin_*，随
    proposal → MemoryItem 全链继承；不传则如实留空（NULL），不猜测。

    返回 {"ingested", "candidate_id", "fastpath", "lane", "status"}
    status：fastpath（直通）/ new（待 evolve gate 分层）/
    pending_review（低置信度 / 提取失败兜底；闲聊直接 rejected）
    """
    # 票 43：通道开关先于输入校验——关掉的通道对任何输入一律拒绝
    assert_dialogue_enabled()
    text = (text or "").strip()
    if not text:
        raise ValueError("empty dialogue text")
    if len(text) > 50000:
        raise ValueError("dialogue text too long (max 50000)")
    # 獬豸（票据 .scratch/xiezhi-injection-guard/01）：写入侧注入检测，命中
    # 整条拒——检测在 fastpath 之前，「记住：<注入>」不因白名单直通而漏闸
    from lantai.security.injection_guard import assert_no_prompt_injection

    assert_no_prompt_injection(text)

    from lantai.security.secret_guard import detect_secret_shapes

    secret_hits = detect_secret_shapes(text)

    # 1) fastpath 白名单直通——绕过 LLM 提取
    fp = fastpath_check(text)
    if fp:
        result = _create_candidate(
            text,
            lane=fp["lane"],
            fp_data=fp,
            status="fastpath",
            user_id=user_id,
            source=source,
            created_at=created_at,
            session_id=session_id,
            turn=turn,
        )
        return _secret_guard_route(result, secret_hits)

    # 2) 闲聊 → 直接 rejected（沙汰，ADR-0026），不进待审队列
    if _is_chitchat(text):
        return _create_candidate(
            text,
            lane="general",
            fp_data=None,
            status="rejected",
            user_id=user_id,
            source=source,
            created_at=created_at,
            session_id=session_id,
            turn=turn,
        )

    # 3) LLM 提取（extract_candidate 自带降级 fallback）→ 走现有 gate 分层
    data = extract_candidate(text[:40], text)
    lane = _guess_lane(text)
    result = _create_candidate(
        text,
        lane=lane,
        fp_data=data,
        status="new",
        user_id=user_id,
        source=source,
        created_at=created_at,
        session_id=session_id,
        turn=turn,
    )
    # 密钥三态路由单一真源（X-06，票 17-4）：调用方不复制 mode 判据——
    # off 由 detect_secret_shapes 内部处理（恒空），shadow/enforce 由
    # route_secret_hits 内部处理；此处无条件委托（与 fastpath 臂同口径）。
    result = _secret_guard_route(result, secret_hits)
    if result["status"] == "pending_review":
        return result
    if data["extractor_confidence"] < settings.DIALOGUE_MIN_EXTRACTOR_CONF:
        if data["extractor_confidence"] < settings.CANDIDATE_MIN_CONFIDENCE:
            # 沙汰：低于地板信噪门 → 直接 rejected（ADR-0026）
            from lantai.models.tables import MemoryCandidate

            with db.get_session() as s:
                c = s.get(MemoryCandidate, result["candidate_id"])
                if c:
                    c.status = "rejected"
                    s.add(c)
                    s.commit()
            result["status"] = "rejected"
        else:
            # 低置信度 / 提取失败兜底 → 待审队列（不丢数据，交用户裁决）
            enqueue_rejected(result["candidate_id"])
            result["status"] = "pending_review"
    return result


def _secret_guard_route(result: dict, secret_hits: list[str]) -> dict:
    """薄壳：路由逻辑单一真源在 `secret_guard.route_secret_hits`（X-06 下沉）。"""
    from lantai.security.secret_guard import route_secret_hits

    return route_secret_hits(result, secret_hits)


def _create_candidate(
    text: str,
    *,
    lane: str,
    fp_data: dict | None,
    status: str,
    user_id: str,
    source: str,
    created_at=None,
    session_id: str = "",
    turn: int | None = None,
) -> dict:
    """建 rawdocument（content_hash 去重复用）→ memorycandidate。

    created_at 非空（冷启动导入）：doc.fetched_at / cand.created_at 用原始
    时间戳，provenance.prompt = dialogue-session-import（演化链据此继承）。

    session_id / turn 非空：cand.session_id 落列 + provenance.origin_*
    落 dict，随 proposal → MemoryItem 全链继承（票据 01）。
    """
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    provenance_prompt = {
        "fastpath": PROVENANCE_PROMPT_DIALOGUE_FASTPATH,
        "pending_review": PROVENANCE_PROMPT_DIALOGUE_CHITCHAT,
        "rejected": PROVENANCE_PROMPT_DIALOGUE_CHITCHAT,
    }.get(status, PROVENANCE_PROMPT_EXTRACT)
    if created_at is not None:
        provenance_prompt = PROVENANCE_PROMPT_DIALOGUE_IMPORT
    origin_extra: dict = {}
    # 更漏（ADR-0048/票 08）：显式事件时间提取尝试——确定性格式命中才写 provenance，
    # 失败不猜（宁 miss 不脏写），U3 标注面：event_time_extract_failed 如实记录
    try:
        from lantai.core.time_precision import extract_explicit_event_time

        et, etp = extract_explicit_event_time(text)
        if et is not None:
            origin_extra["event_time"] = et.isoformat()
            origin_extra["event_time_precision"] = etp
        else:
            origin_extra["event_time_extract_failed"] = True
    except Exception:
        # 票 17-5：提取器异常不再静默吞——异常同属「尝试失败」，按上方设计
        # 意图如实留痕（日志）+ 落失败标记（U3 标注面），宁 miss 不脏写不变
        origin_extra["event_time_extract_failed"] = True
        logger.warning("event_time 提取异常（dialogue），已标记 failed", exc_info=True)
    if (session_id or "").strip():
        origin_extra["origin_session_id"] = session_id.strip()
    if turn is not None:
        origin_extra["origin_turn"] = int(turn)
    if origin_extra:
        origin_extra["origin_source"] = source
    with db.get_session() as s:
        doc = s.exec(select(RawDocument).where(RawDocument.content_hash == h)).first()
        if not doc:
            doc_kwargs = dict(
                id=new_id("doc"),
                source_type="dialogue",
                source_id=source,
                url="",
                title=text[:40],
                content=text,
                content_hash=h,
                meta={"user_id": user_id, "source": source},
            )
            if created_at is not None:
                doc_kwargs["fetched_at"] = created_at
            doc = RawDocument(**doc_kwargs)
            s.add(doc)
            s.commit()
            s.refresh(doc)

        cand_kwargs = dict(
            id=new_id("cand"),
            user_id=user_id or None,
            session_id=(session_id or "").strip() or None,
            document_id=doc.id,
            topic=fp_data.get("topic") if fp_data else [],
            summary=(fp_data.get("summary") if fp_data else None) or text[:400],
            claims=fp_data.get("claims", []) if fp_data else [],
            methods=fp_data.get("methods", []) if fp_data else [],
            constraints=fp_data.get("constraints", []) if fp_data else [],
            actions=fp_data.get("actions", []) if fp_data else [],
            extractor_confidence=(fp_data.get("extractor_confidence", 0.0) if fp_data else 0.0),
            provenance=make_provenance(provenance_prompt, extra=origin_extra or None),
            lane=lane,
            status=status,
        )
        if created_at is not None:
            cand_kwargs["created_at"] = created_at
        cand = MemoryCandidate(**cand_kwargs)
        if status == "pending_review":
            cand.review_due_at = utcnow() + timedelta(days=settings.CANDIDATE_TTL_DAYS)
        s.add(cand)
        s.commit()
        s.refresh(cand)
        return {
            "ingested": True,
            "candidate_id": cand.id,
            "fastpath": status == "fastpath",
            "lane": lane,
            "status": cand.status,
        }
