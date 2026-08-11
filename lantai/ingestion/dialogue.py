"""对话写通道（Ticket 01）——让记忆从对话中自动长出来

复用现有 rawdocument → memorycandidate 链，不新建存储、
不改 fastpath / gate 语义：

1. fastpath 白名单直通（"记住：X" / 自我声明 / 偏好表达）——绕过 LLM 提取
2. 闲聊（过短 / 纯社交结束语）——直接建兜底候选进待审队列（不落库为记忆）
3. 其余文本——LLM 提取建候选（status=new，走现有 gate 分层）；
   低置信度 / 提取失败（上游 502）→ 候选进待审队列，不丢数据
"""
import hashlib
import re
from datetime import timedelta

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.gate.prefilter import NO_MEMORY_PATTERNS
from lantai.core.provenance import (
    PROVENANCE_PROMPT_DIALOGUE_CHITCHAT, PROVENANCE_PROMPT_DIALOGUE_FASTPATH,
    PROVENANCE_PROMPT_DIALOGUE_IMPORT, PROVENANCE_PROMPT_EXTRACT,
    make_provenance)
from lantai.models.tables import RawDocument, MemoryCandidate
from lantai.parsing.extractor import extract_candidate
from lantai.parsing.fastpath import fastpath_check
from lantai.services.candidate_service import enqueue_rejected
from lantai.storage import db

# 对话入口的 lane 预判（宽松 search；fastpath 整段 match 语义保持不变）
_PREFERENCE_RE = re.compile(
    r"我.{0,8}?(?:喜欢|不喜欢|讨厌|偏爱|偏好)")
_FACT_RE = re.compile(
    r"^我是|^我叫|我的(?:名字|生日|年龄|地址|电话|邮箱)|我住在|我来自")


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


def ingest_dialogue(text: str, *, user_id: str = "default",
                    source: str = "dialogue",
                    created_at=None) -> dict:
    """对话文本 → 现有提取链。

    created_at：冷启动导入时传原始消息时间戳（naive UTC）——RawDocument/
    MemoryCandidate 用该时间，provenance.prompt=dialogue-session-import，
    随演化链继承到 MemoryItem（时间线不压平）。

    返回 {"ingested", "candidate_id", "fastpath", "lane", "status"}
    status：fastpath（直通）/ new（待 evolve gate 分层）/
    pending_review（闲聊 / 低置信度 / 提取失败兜底）
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty dialogue text")
    if len(text) > 50000:
        raise ValueError("dialogue text too long (max 50000)")

    # 1) fastpath 白名单直通——绕过 LLM 提取
    fp = fastpath_check(text)
    if fp:
        return _create_candidate(text, lane=fp["lane"], fp_data=fp,
                                 status="fastpath", user_id=user_id,
                                 source=source, created_at=created_at)

    # 2) 闲聊 → 兜底候选进待审队列（不静默丢弃、不落库为记忆）
    if _is_chitchat(text):
        return _create_candidate(text, lane="general", fp_data=None,
                                 status="pending_review", user_id=user_id,
                                 source=source, created_at=created_at)

    # 3) LLM 提取（extract_candidate 自带降级 fallback）→ 走现有 gate 分层
    data = extract_candidate(text[:40], text)
    lane = _guess_lane(text)
    result = _create_candidate(text, lane=lane, fp_data=data, status="new",
                               user_id=user_id, source=source,
                               created_at=created_at)
    if data["extractor_confidence"] < settings.DIALOGUE_MIN_EXTRACTOR_CONF:
        # 低置信度 / 提取失败兜底 → 待审队列（不丢数据，交用户裁决）
        enqueue_rejected(result["candidate_id"])
        result["status"] = "pending_review"
    return result


def _create_candidate(text: str, *, lane: str, fp_data: dict | None,
                      status: str, user_id: str, source: str,
                      created_at=None) -> dict:
    """建 rawdocument（content_hash 去重复用）→ memorycandidate。

    created_at 非空（冷启动导入）：doc.fetched_at / cand.created_at 用原始
    时间戳，provenance.prompt = dialogue-session-import（演化链据此继承）。
    """
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    provenance_prompt = {
        "fastpath": PROVENANCE_PROMPT_DIALOGUE_FASTPATH,
        "pending_review": PROVENANCE_PROMPT_DIALOGUE_CHITCHAT,
    }.get(status, PROVENANCE_PROMPT_EXTRACT)
    if created_at is not None:
        provenance_prompt = PROVENANCE_PROMPT_DIALOGUE_IMPORT
    with db.get_session() as s:
        doc = s.exec(select(RawDocument)
                     .where(RawDocument.content_hash == h)).first()
        if not doc:
            doc_kwargs = dict(
                id=new_id("doc"), source_type="dialogue", source_id=source,
                url="", title=text[:40], content=text, content_hash=h,
                meta={"user_id": user_id, "source": source},
            )
            if created_at is not None:
                doc_kwargs["fetched_at"] = created_at
            doc = RawDocument(**doc_kwargs)
            s.add(doc); s.commit(); s.refresh(doc)

        cand_kwargs = dict(
            id=new_id("cand"), document_id=doc.id,
            topic=fp_data.get("topic") if fp_data else [],
            summary=(fp_data.get("summary") if fp_data else None) or text[:400],
            claims=fp_data.get("claims", []) if fp_data else [],
            methods=fp_data.get("methods", []) if fp_data else [],
            constraints=fp_data.get("constraints", []) if fp_data else [],
            actions=fp_data.get("actions", []) if fp_data else [],
            extractor_confidence=(fp_data.get("extractor_confidence", 0.0)
                                  if fp_data else 0.0),
            provenance=make_provenance(provenance_prompt),
            lane=lane, status=status,
        )
        if created_at is not None:
            cand_kwargs["created_at"] = created_at
        cand = MemoryCandidate(**cand_kwargs)
        if status == "pending_review":
            cand.review_due_at = utcnow() + timedelta(
                days=settings.CANDIDATE_TTL_DAYS)
        s.add(cand); s.commit(); s.refresh(cand)
        return {"ingested": True, "candidate_id": cand.id,
                "fastpath": status == "fastpath", "lane": lane,
                "status": cand.status}
