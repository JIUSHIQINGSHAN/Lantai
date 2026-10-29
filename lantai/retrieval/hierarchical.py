"""抽象层级化与选层检索模型（Hierarchical Abstraction & Layering）。

依据 MCMA 论文（arXiv:2601.07470 · docs/research/papers/notes/07-mcma-reading.md §8 建议项 4）：
将记忆划分为三层认识论抽象：
- H0 (Raw Observation): 原始观察、未加工的交互草稿、临时对话流水（role=OBSERVATION, lane in ("chat", "general")）
- H1 (Episodic / Fact): 具体情境事实、业务经验、完璧原子事实（FactAtomic, role=EXPERIENCE, lane in ("fact", "experience")）
- H2 (Abstract Principle): 通用规则、行为准则、信念、技能结晶（role=RULE/BELIEF, SkillCrystal, lane in ("rule", "preference")）

支持根据 Query 抽象意图自适应提升匹配层级（layer_boost），并向检索结果透明透出 layer 字段。
"""

from typing import Any, Literal

from lantai.models.tables import CognitiveRole, FactAtomic, MemoryItem

HierarchicalLayer = Literal["h0", "h1", "h2"]
QueryIntentLayer = Literal["h0", "h1", "h2", "all"]

# 抽象意图关键词（倾向触发高阶 H2 规则/准则/规范）
ABSTRACT_INTENT_KEYWORDS = frozenset(
    {
        "原则",
        "规范",
        "准则",
        "规矩",
        "指南",
        "要求",
        "最佳实践",
        "怎么做",
        "如何做",
        "约定",
        "标准",
        "铁律",
        "纪律",
        "方法论",
        "principle",
        "rule",
        "guideline",
        "standard",
        "convention",
        "best practice",
    }
)

# 具象意图关键词（倾向触发 H1 具体情境事实/时间/人员事实）
CONCRETE_INTENT_KEYWORDS = frozenset(
    {
        "担任",
        "职位",
        "上线",
        "完成",
        "发生",
        "何时",
        "哪一天",
        "哪年",
        "谁做",
        "负责",
        "职务",
        "事件",
        "具体",
        "position",
        "event",
        "who",
        "when",
    }
)


def classify_memory_layer(item: Any) -> HierarchicalLayer:
    """判定单条记忆实体所归属的认识论抽象层级（h0 / h1 / h2）。

    - h0: 原始观察（Observation、草稿、临时流水）
    - h1: 具体情境事实（FactAtomic、Experience、具体经验）
    - h2: 高阶抽象规则与原则（Rule、Belief、SkillCrystal、通用偏好）
    """
    if isinstance(item, FactAtomic):
        return "h1"

    role = getattr(item, "role", None)
    lane = getattr(item, "lane", "") or ""

    # 1. 优先判定 H2 抽象规则
    if role in (CognitiveRole.RULE, CognitiveRole.BELIEF, "rule", "belief"):
        return "h2"
    if lane in ("rule", "preference"):
        return "h2"

    # 2. 判定 H1 具体事实与经验
    if role in (CognitiveRole.EXPERIENCE, "experience"):
        return "h1"
    if lane in ("fact", "experience"):
        return "h1"

    # 3. 判定 H0 原始观察
    if role in (CognitiveRole.OBSERVATION, "observation"):
        return "h0"
    if lane in ("chat", "general"):
        return "h0"

    # 默认按情境事实 H1 解释（宁多见事实，不虚标高阶规则）
    return "h1"


def detect_query_intent_layer(query: str) -> QueryIntentLayer:
    """分析 Query 的意图抽象度，返回建议激活的层级（h1 / h2 / all）。

    - "h2": 询问高阶规范、原则、方法论、准则类问题
    - "h1": 询问具体实体、职务、时间、业务事实类问题
    - "all": 无明显层级偏向或综合性查询
    """
    if not query:
        return "all"

    q_lower = query.lower()

    # 检查是否命中了抽象关键词
    has_abstract = any(kw in q_lower for kw in ABSTRACT_INTENT_KEYWORDS)
    # 检查是否命中了具象关键词
    has_concrete = any(kw in q_lower for kw in CONCRETE_INTENT_KEYWORDS)

    if has_abstract and not has_concrete:
        return "h2"
    if has_concrete and not has_abstract:
        return "h1"

    return "all"


def filter_and_resolve_layer(items: list, query: str, params: Any) -> tuple[list, str]:
    """统一解析目标抽象层级，并在显式指定且非空时执行过滤，消除多通道重复代码。"""
    target = getattr(params, "hierarchical_layer", None) or "auto"
    if target == "auto":
        target = detect_query_intent_layer(query)
    elif target in ("h0", "h1", "h2"):
        filtered = [m for m in items if classify_memory_layer(m) == target]
        if filtered:
            items = filtered
    return items, target
