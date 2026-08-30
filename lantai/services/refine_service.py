"""披沙（ADR-0030）：候选记忆递归精炼（Refine）核心服务。

提供：
1. refine_memory_text: LLM 指代消解、事实提纯与置信度重估（纯函数 + 降级保护）；
2. refine_candidate_record: 针对单条候选记录进行精炼并更新 DB；
3. batch_refine_candidates: 针对模糊区间候选批量披沙提纯。
"""
from typing import Optional
from sqlmodel import Session, select

from lantai.core.logger import logger
from lantai.llm import client as llm_client
from lantai.models.tables import MemoryCandidate
from lantai.storage import db

REFINE_SYSTEM_PROMPT = """你是一个严谨的记忆精炼与事实提纯专家（兰台记忆·披沙系统）。
你的任务是对提取出的模糊候选记忆进行指代消解、事实提纯与去噪。

【精炼要求】
1. 指代消解：将“他/它/那个/这件事”等弱指代根据上下文还原为具体的主语、实体名称或技术栈；
2. 去粗取精：剔除口语化副词、助词、客套修饰，提炼为精炼且信息完整的陈述句；
3. 原子化收敛：确保记忆表达单一、清晰的事实、偏好或规则；
4. 真实性与置信度重估：
   - 若提纯后事实确凿且实体完整：输出 is_valid=true，confidence 置为 0.70~0.95；
   - 若原内容纯属无意义社交客套、闲聊废话：输出 is_valid=false，confidence=0.0；
5. 必须返回严格的 JSON 格式：
{
  "refined_text": "精炼后的记忆陈述句",
  "confidence": 0.85,
  "is_valid": true,
  "lane": "general",
  "tags": ["标签1"],
  "reason": "精炼与提纯说明"
}
"""


def refine_memory_text(text: str, context: str = "", metadata: Optional[dict] = None) -> dict:
    """对单段记忆文本执行指代消解与结构化提纯（纯函数，异常时宁 miss 不脏写降级）。"""
    raw_text = (text or "").strip()
    if not raw_text:
        return {
            "refined_text": "",
            "confidence": 0.0,
            "is_valid": False,
            "lane": "general",
            "tags": [],
            "reason": "输入文本为空",
        }

    user_prompt = f"【待精炼候选文本】:\n{raw_text}\n"
    if context:
        user_prompt += f"\n【上下文背景】:\n{context}\n"

    try:
        res = llm_client.chat_json(REFINE_SYSTEM_PROMPT, user_prompt)
        if isinstance(res, dict) and "refined_text" in res:
            refined_text = str(res.get("refined_text", "")).strip() or raw_text
            confidence = float(res.get("confidence", 0.7))
            is_valid = bool(res.get("is_valid", True))
            lane = str(res.get("lane", "general"))
            tags = list(res.get("tags", []))
            reason = str(res.get("reason", "精炼成功"))
            return {
                "refined_text": refined_text,
                "confidence": min(max(confidence, 0.0), 1.0),
                "is_valid": is_valid,
                "lane": lane if lane in ("general", "profile", "preference", "rule", "entity", "project") else "general",
                "tags": [str(t).strip() for t in tags if str(t).strip()],
                "reason": reason,
            }
    except Exception as exc:
        logger.warning("披沙：LLM 精炼调用异常（优雅降级保持原样）: %s", exc)

    # 优雅降级（宁 miss 不脏写）
    return {
        "refined_text": raw_text,
        "confidence": 0.5,
        "is_valid": True,
        "lane": "general",
        "tags": [],
        "reason": "LLM 调用异常，优雅降级保持原候选文本",
    }


def refine_candidate_record(candidate_id: str, session: Optional[Session] = None) -> dict:
    """对指定 ID 的候选记录执行精炼并落库。"""
    cand_id = (candidate_id or "").strip()
    if not cand_id:
        raise ValueError("candidate_id 不能为空")

    def _run(s: Session) -> dict:
        cand = s.get(MemoryCandidate, cand_id)
        if not cand:
            raise ValueError(f"候选记录未找到: {cand_id}")

        raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
        context_str = str(cand.provenance) if cand.provenance else ""
        res = refine_memory_text(raw_text, context=context_str)

        if not res["is_valid"] or res["confidence"] <= 0.0:
            cand.status = "rejected"
        else:
            cand.summary = res["refined_text"]
            cand.claims = [res["refined_text"]]
            cand.extractor_confidence = res["confidence"]
            cand.lane = res.get("lane") or cand.lane

        s.add(cand)
        s.commit()
        s.refresh(cand)
        logger.info("披沙：候选【%s】精炼完成（status=%s, conf=%.2f）", cand.id, cand.status, cand.extractor_confidence)
        return cand.model_dump(mode="json")

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def batch_refine_candidates(
    min_conf: float = 0.15,
    max_conf: float = 0.6,
    limit: int = 20,
    session: Optional[Session] = None,
) -> dict:
    """批量对处于模糊置信度区间的候选执行披沙提纯。"""
    def _run(s: Session) -> dict:
        candidates = s.exec(
            select(MemoryCandidate)
            .where(
                MemoryCandidate.status == "pending_review",
                MemoryCandidate.extractor_confidence >= min_conf,
                MemoryCandidate.extractor_confidence <= max_conf,
            )
            .order_by(MemoryCandidate.created_at.desc())
            .limit(limit)
        ).all()

        refined_count = 0
        rejected_count = 0
        for cand in candidates:
            raw_text = cand.summary or (cand.claims[0] if cand.claims else "")
            context_str = str(cand.provenance) if cand.provenance else ""
            res = refine_memory_text(raw_text, context=context_str)
            if not res["is_valid"] or res["confidence"] <= 0.0:
                cand.status = "rejected"
                rejected_count += 1
            else:
                cand.summary = res["refined_text"]
                cand.claims = [res["refined_text"]]
                cand.extractor_confidence = res["confidence"]
                cand.lane = res.get("lane") or cand.lane
                refined_count += 1
            s.add(cand)

        s.commit()
        return {
            "total_scanned": len(candidates),
            "refined": refined_count,
            "rejected": rejected_count,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)
