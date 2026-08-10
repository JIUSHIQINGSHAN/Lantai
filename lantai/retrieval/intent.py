"""意图分类：判断 query 属于 fact lookup / procedural / exploratory"""
from lantai.core.settings import settings
from lantai.llm.client import chat_json

INTENT_SYS = """你是一个搜索意图分类器。给定用户 query，判断其搜索意图类型：
- "fact_lookup"：查找具体事实、定义、数据（短 query，通常 ≤5 词）
- "procedural"：查找步骤、方法、教程（含"怎么"、"如何"、"步骤"等词）
- "exploratory"：广泛探索、对比、综述（长 query，含多个概念）

只返回一个 JSON: {"intent": "fact_lookup"|"procedural"|"exploratory", "reason": "一句话解释"}"""


def classify_intent(query: str) -> dict:
    """分类 query 返回意图类型和候选集大小"""
    try:
        data = chat_json(INTENT_SYS, query)
        intent = data.get("intent", settings.DEFAULT_INTENT)
        if intent not in settings.INTENT_CANDIDATE_SIZES:
            intent = settings.DEFAULT_INTENT
    except Exception:
        intent = settings.DEFAULT_INTENT
    return {
        "intent": intent,
        "candidate_n": settings.INTENT_CANDIDATE_SIZES.get(intent, 10),
    }
