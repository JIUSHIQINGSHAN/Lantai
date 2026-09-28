"""AI 自动辅助审阅（Auto-Triage）核心服务。

遵循「人机协同·推荐预判制」与「宁 miss 不脏写」纪律：
1. 扫描待审候选（MemoryCandidate）、提案等；
2. 批量调用 LLM 生成结构化研判建议（action: approve | reject | refine | manual, reason, score）；
3. 支持一键或批量执行裁决。
"""

from typing import Any

from sqlmodel import Session, select

from lantai.core.logger import logger
from lantai.llm import client as llm_client
from lantai.models.tables import MemoryCandidate
from lantai.services.candidate_service import review_candidate
from lantai.services.refine_service import refine_candidate_record
from lantai.storage import db

TRIAGE_SYSTEM_PROMPT = """你是一个专业的长程记忆管理与知识审阅专家（兰台记忆·案牍审阅系统）。
你的任务是对系统自动提取的候选记忆进行质量研判与决策建议。

【研判准则】
1. approve（建议批准）：
   - 包含明确的事实、用户稳定偏好、行为准则、专有名词定义或项目规则。
   - 表达清晰，主体明确，置信度高（>=0.75）。
2. reject（建议淘汰）：
   - 纯社交客套、寒暄废话（如“好的”、“收到”、“哈哈”）、瞬时临时上下文（如“我刚刚喝了杯水”）、或缺乏长程记忆价值的内容。
3. refine（建议提纯）：
   - 包含有价值的事实，但存在“他/这个工具/那个事情”等弱指代不清，或口语化修饰过多需要精炼。
4. manual（建议人工复核）：
   - 存在争议、疑似冲突或事实边界不明确，需要人类维护者亲自定夺。

【输入格式】
候选列表 JSON 数组：[{"id": "...", "text": "...", "confidence": 0.5, "lane": "general"}]

【输出格式】
必须输出严格的 JSON 格式：
{
  "recommendations": [
    {
      "id": "候选ID",
      "action": "approve" | "reject" | "refine" | "manual",
      "confidence_score": 0.85,
      "reason": "研判理由（简明扼要，10~20字）"
    }
  ]
}
"""


def triage_candidates_batch(
    candidates_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """批量对候选列表执行 LLM 智能研判（纯函数 + 降级保护）。"""
    if not candidates_data:
        return []

    user_prompt = f"【待研判候选列表】:\n{candidates_data}\n"

    try:
        res = llm_client.chat_json(TRIAGE_SYSTEM_PROMPT, user_prompt)
        if isinstance(res, dict) and "recommendations" in res:
            recs = res.get("recommendations", [])
            rec_map = {str(r.get("id")): r for r in recs if isinstance(r, dict)}

            output = []
            for c in candidates_data:
                cid = str(c.get("id"))
                if cid in rec_map:
                    r = rec_map[cid]
                    output.append(
                        {
                            "id": cid,
                            "action": str(r.get("action", "manual")),
                            "confidence_score": float(
                                r.get("confidence_score", c.get("confidence", 0.5))
                            ),
                            "reason": str(r.get("reason", "LLM 建议复核")),
                        }
                    )
                else:
                    # 规则启发式保底
                    conf = float(c.get("confidence", 0.5))
                    action = "approve" if conf >= 0.8 else ("reject" if conf < 0.2 else "manual")
                    output.append(
                        {
                            "id": cid,
                            "action": action,
                            "confidence_score": conf,
                            "reason": f"规则保底判定 (conf={conf:.2f})",
                        }
                    )
            return output
    except Exception as exc:
        logger.warning("AI 智能预审 LLM 调用异常（降级至启发式规则）: %s", exc)

    # 降级策略（规则启发式）
    fallback_output = []
    for c in candidates_data:
        cid = str(c.get("id"))
        conf = float(c.get("confidence", 0.5))
        text = str(c.get("text", "")).strip()

        # 简单字数或客套检测
        if len(text) < 4 or any(w in text for w in ["好的", "嗯嗯", "收到", "再见", "哈哈"]):
            action = "reject"
            reason = "疑似纯客套或瞬时内容"
        elif conf >= 0.75:
            action = "approve"
            reason = "高置信度事实"
        elif conf >= 0.4:
            action = "refine"
            reason = "内容有价值但需提纯"
        else:
            action = "reject"
            reason = "低置信度碎片"

        fallback_output.append(
            {
                "id": cid,
                "action": action,
                "confidence_score": conf,
                "reason": reason,
            }
        )
    return fallback_output


def run_ai_triage(
    limit: int = 50,
    session: Session | None = None,
    *,
    principal=None,
) -> dict[str, Any]:
    """扫描数据库中所有 pending_review 的候选并生成 AI 预审建议清单。

    归属收窄（票 .scratch/readside-gaps/02）：`/candidates/ai_triage` 把
    `text`（候选正文）直接送给 LLM，此前全表捞——A 能借这个端点读到
    B 的待审候选。同 `list_pending_candidates` 口径按 viewer 收窄。
    """

    def _run(s: Session) -> dict[str, Any]:
        from lantai.services.candidate_service import _owner_scope

        conditions = [MemoryCandidate.status == "pending_review"]
        scope = _owner_scope(principal)
        if scope is not None:
            conditions.append(scope)
        candidates = s.exec(
            select(MemoryCandidate)
            .where(*conditions)
            .order_by(MemoryCandidate.created_at.desc())
            .limit(limit)
        ).all()

        if not candidates:
            return {"total": 0, "recommendations": []}

        payload = []
        for c in candidates:
            text = c.summary or (c.claims[0] if c.claims else "")
            payload.append(
                {
                    "id": c.id,
                    "text": text,
                    "confidence": c.extractor_confidence or 0.5,
                    "lane": c.lane or "general",
                }
            )

        recommendations = triage_candidates_batch(payload)
        return {
            "total": len(candidates),
            "recommendations": recommendations,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def apply_ai_triage_batch(
    actions: list[dict[str, Any]],
    *,
    principal=None,
) -> dict[str, Any]:
    """批量执行用户确认的 AI 预审裁决决策。

    actions 格式: [{"id": "cand_1", "action": "approve" | "reject" | "refine", "reason": "..."}]

    归属校验（票 .scratch/readside-gaps/02）：这个入口最阴——请求体里的
    `id` 是**调用方给的**，与预审时返回的建议列表没有任何绑定关系。
    也就是说 A 根本不需要预审到 B 的候选，直接 POST 一个 `{"id":
    "cand-B", "action": "reject"}` 就能驳回它。校验下传给 review_candidate
    / refine_candidate_record（那两处已有），这里只负责传身份。
    """
    applied = {"approved": 0, "rejected": 0, "refined": 0, "failed": 0}

    for item in actions:
        cid = item.get("id")
        act = item.get("action")
        reason = item.get("reason", "AI 批量预审采纳")
        if not cid or not act:
            continue

        try:
            if act == "approve":
                review_candidate(cid, approve=True, reason=reason, principal=principal)
                applied["approved"] += 1
            elif act == "reject":
                review_candidate(cid, approve=False, reason=reason, principal=principal)
                applied["rejected"] += 1
            elif act == "refine":
                refine_candidate_record(cid, principal=principal)
                applied["refined"] += 1
        except Exception as exc:
            logger.warning("批量应用 AI 预审失败 [%s -> %s]: %s", cid, act, exc)
            applied["failed"] += 1

    return applied


def run_triage_auto_pilot(
    min_approve_conf: float = 0.85,
    max_reject_conf: float = 0.25,
    limit: int = 50,
    dry_run: bool = False,
    *,
    principal=None,
) -> dict[str, Any]:
    """「持节」· 智能体案牍巡检官一键自治（Auto-Pilot）：

    1. 扫描 pending_review 待审候选；
    2. 执行 LLM 智能研判与置信度评估；
    3. 自动归档低信噪比/闲聊噪音（<= max_reject_conf）；
    4. 自动提纯中段模糊事实；
    5. 自动批准确凿高价值记忆（>= min_approve_conf）；
    6. 返回全流程治理统计报告。

    归属（票 `.scratch/mcp-identity-gaps/01c`）：本函数此前四个参数一个
    身份都不收，而它内部两步都踩在已修好的收窄逻辑上——`run_ai_triage`
    与 `apply_ai_triage_batch` **都已有** `principal` 形参（票
    readside-gaps/02），只是没往下传。同一个函数三个入口两种口径：
    HTTP `/candidates/ai_triage` 与 MCP `triage_analyze` 都按调用方
    收窄，auto-pilot 这一路是 `principal=None`。

    **影响面按探针实测，不要按代码形状推算**：`_owner_scope(None)` 经
    `_viewer_of(None)` **收敛到字面量 `"default"`，不是全表**。所以泄漏
    的是"任意调用方裁决 `user_id == 'default'` 的那批候选"——真实库
    37 行 memorycandidate 全部是 'default'，单用户部署下这一路摸到的
    正是全部历史候选。B（显式属主）的候选被收敛挡住了，是安全的。

    `principal=None`（内部 worker/CLI）保持原状——同族口径。
    """
    triage_result = run_ai_triage(limit=limit, principal=principal)
    recommendations = triage_result.get("recommendations", [])

    actions_to_apply = []
    summary = {
        "scanned": len(recommendations),
        "auto_approved": 0,
        "auto_rejected": 0,
        "auto_refined": 0,
        "kept_pending": 0,
        "dry_run": dry_run,
        "details": [],
    }

    for r in recommendations:
        cid = r["id"]
        action = r["action"]
        score = r["confidence_score"]
        reason = r["reason"]

        final_act = "manual"
        if action == "reject" or score <= max_reject_conf:
            final_act = "reject"
            summary["auto_rejected"] += 1
        elif action == "approve" and score >= min_approve_conf:
            final_act = "approve"
            summary["auto_approved"] += 1
        elif action == "refine":
            final_act = "refine"
            summary["auto_refined"] += 1
        else:
            summary["kept_pending"] += 1

        summary["details"].append(
            {
                "id": cid,
                "decision": final_act,
                "score": score,
                "reason": reason,
            }
        )

        if final_act in ("approve", "reject", "refine"):
            actions_to_apply.append(
                {
                    "id": cid,
                    "action": final_act,
                    "reason": f"[持节·AutoPilot] {reason}",
                }
            )

    if not dry_run and actions_to_apply:
        applied_stats = apply_ai_triage_batch(actions_to_apply, principal=principal)
        summary["applied_stats"] = applied_stats

    return summary
