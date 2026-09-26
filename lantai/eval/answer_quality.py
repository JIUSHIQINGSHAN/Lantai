"""回答层计分（P0 票05，LongMemEval 式召回/回答分层）。

召回层只回答「检索结果对不对」；回答层回答「LLM 拿这些记忆能不能答对」。
两层数值分开呈现，不做单变量混合分。

判官：
- rule_judge：确定性要点命中率（子串匹配，零依赖，离线 CI 可跑）
- llm_judge：LLM 判官打分（外部依赖，mock 责任在调用方；--judge llm 选配）

诚实原则：要点命中是保守下界——同义改写未被命中会计 miss；不做 LLM 自评膨胀。
"""

from lantai.core.logger import logger
from lantai.llm.client import chat_json
from lantai.services.prompt_service import get_prompt


def rule_judge(query: str, retrieved_contents: list[str], key_points: list[str]) -> dict:
    """确定性判官：key_points 是否出现在召回正文（子串命中）。

    返回 {"hit_points", "points", "hit_rate", "missed"}；key_points 为空时
    hit_rate=None（不计分，不编造）。
    """
    points = [str(p) for p in (key_points or []) if str(p).strip()]
    if not points:
        return {"hit_points": 0, "points": 0, "hit_rate": None, "missed": []}
    corpus = "\n".join(str(c) for c in (retrieved_contents or []))
    hit = [p for p in points if p in corpus]
    missed = [p for p in points if p not in corpus]
    return {
        "hit_points": len(hit),
        "points": len(points),
        "hit_rate": round(len(hit) / len(points), 4),
        "missed": missed,
    }


LLM_JUDGE_SYS = (
    "You are a strict grader. Given a question, retrieved memory snippets, and the "
    "key points of the expected answer, decide for EACH key point whether the "
    "snippets contain the information needed to state it. Judge only by the "
    "snippets; do not use your own knowledge. Return strict JSON: "
    '{"covered": [true/false ...]}. The array length must equal the number of key points.'
)


def llm_judge(query: str, retrieved_contents: list[str], key_points: list[str]) -> dict:
    """LLM 判官：逐要点判定覆盖（外部 LLM；异常降级为 rule_judge，宁降级不编造）。"""
    points = [str(p) for p in (key_points or []) if str(p).strip()]
    if not points:
        return {"hit_points": 0, "points": 0, "hit_rate": None, "missed": []}
    snippets = "\n".join(f"- {str(c)[:200]}" for c in (retrieved_contents or [])) or "(none)"
    numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points))
    user = f"QUESTION: {query}\n\nRETRIEVED SNIPPETS:\n{snippets}\n\nKEY POINTS:\n{numbered}"
    try:
        data = chat_json(get_prompt("ANSWER_JUDGE_SYS", LLM_JUDGE_SYS), user)
        covered = data.get("covered")
        if not isinstance(covered, list) or len(covered) != len(points):
            raise ValueError("judge returned malformed covered array")
        hit = sum(1 for c in covered if c is True)
        missed = [p for p, c in zip(points, covered) if c is not True]
        return {
            "hit_points": hit,
            "points": len(points),
            "hit_rate": round(hit / len(points), 4),
            "missed": missed,
            "judge": "llm",
        }
    except Exception:
        logger.exception("llm_judge failed; falling back to rule_judge")
        fallback = rule_judge(query, retrieved_contents, points)
        fallback["judge"] = "rule_fallback"
        return fallback


def compute_answer_metrics(per_query: list[dict]) -> dict:
    """纯函数：回答层聚合——按要点加权命中率（分维度 + 总体）。

    只聚合带 answer 字段的条目；无任何计分条目时 overall=None（诚实缺省）。
    """
    by_category: dict[str, dict] = {}
    total_hit = 0
    total_points = 0
    scored = 0
    for q in per_query or []:
        ans = q.get("answer")
        if not ans or ans.get("hit_rate") is None:
            continue
        scored += 1
        cat = q["category"]
        stat = by_category.setdefault(cat, {"hit_points": 0, "points": 0})
        stat["hit_points"] += int(ans.get("hit_points") or 0)
        stat["points"] += int(ans.get("points") or 0)
        total_hit += int(ans.get("hit_points") or 0)
        total_points += int(ans.get("points") or 0)
    for cat, stat in by_category.items():
        stat["hit_rate"] = round(stat["hit_points"] / stat["points"], 4) if stat["points"] else None
    overall = round(total_hit / total_points, 4) if total_points else None
    return {
        "scored_queries": scored,
        "overall_hit_rate": overall,
        "by_category": by_category,
    }
