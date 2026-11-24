"""意图分类：判断 query 属于 fact lookup / procedural / exploratory"""

from lantai.core.settings import settings
from lantai.llm.client import chat_json

INTENT_SYS = """你是一个搜索意图分类器。给定用户 query，判断其搜索意图类型：
- "fact_lookup"：查找具体事实、定义、数据（短 query，通常 ≤5 词）
- "procedural"：查找步骤、方法、教程（含"怎么"、"如何"、"步骤"等词）
- "exploratory"：广泛探索、对比、综述（长 query，含多个概念）

只返回一个 JSON: {"intent": "fact_lookup"|"procedural"|"exploratory", "reason": "一句话解释"}"""


def _default_intent() -> dict:
    """默认意图结果（LANTAI_INTENT_OFF、超预算、异常三条路径共用）。"""
    return {
        "intent": settings.DEFAULT_INTENT,
        "candidate_n": settings.INTENT_CANDIDATE_SIZES.get(settings.DEFAULT_INTENT, 10),
    }


def classify_intent(query: str, timeout: float | None = None) -> dict:
    """分类 query 返回意图类型和候选集大小。

    timeout（秒）：预算传导（票 90）——检索链有外层预算时（钩子 8s 墙）必须
    把剩余预算传进来。此前 `hybrid_search` 的 `budget_s` 只给了 `embed`，意图
    分类同步吃掉整段 LLM 延迟（本机实测 11.0s），默认配置下每轮都撞破预算、
    降级成画像兜底并丢掉 `event_id`，回执链整条断掉。

    `chat_json` 在预算模式（timeout 非 None）下**不做多次重试**：一次机会即止，
    失败抛给本函数的 except 走 `DEFAULT_INTENT`——与 embed 的预算语义同口径。
    """
    import os as _os

    if _os.environ.get("LANTAI_INTENT_OFF") == "1":
        return _default_intent()
    try:
        data = (
            chat_json(INTENT_SYS, query)
            if timeout is None
            else chat_json(INTENT_SYS, query, timeout=timeout)
        )
        intent = data.get("intent", settings.DEFAULT_INTENT)
        if intent not in settings.INTENT_CANDIDATE_SIZES:
            intent = settings.DEFAULT_INTENT
    except Exception:
        intent = settings.DEFAULT_INTENT
    return {
        "intent": intent,
        "candidate_n": settings.INTENT_CANDIDATE_SIZES.get(intent, 10),
    }
