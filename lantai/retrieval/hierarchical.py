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


def count_injected_knowledge_units(entry: Any) -> int:
    """计算单条注入条目所占用的知识单元计数（完璧保整契约）。

    依据 ADR-0057「完璧」原则与 MCMA 建议项 6：
    一个 FactAtomic 原子事实块（无论挂载了多少个投影实体）始终作为 1 个保整的知识单元，
    普通记忆项亦为 1 个单元，空条目或无效项计 0。
    """
    if not entry:
        return 0
    return 1


# 信息密度层级权重乘子（MCMA 建议项 6：H2 抽象规则 > H1 事实 > H0 流水草稿）
LAYER_DENSITY_MULTIPLIER: dict[HierarchicalLayer, float] = {
    "h2": 1.25,  # 高阶准则、规则、原则：信息密度与迁移泛化性最高
    "h1": 1.00,  # 具体情境事实、完璧原子事实：具体业务依据
    "h0": 0.80,  # 原始观察流水、未加工对话：信息密度较低，优先淘汰
}


def rerank_by_information_density(candidates: list[dict], max_items: int = 5) -> list[dict]:
    """依据信息密度优先重排并严格硬顶（MCMA 建议项 6 与磨勘考功纪律）。

    在相似度打分的基础上，结合认知抽象层级与原子事实保整，
    计算 density_score = score * LAYER_DENSITY_MULTIPLIER[layer]，
    并额外对拥有完璧原子事实（atomic_facts）的条目给予 1.1x 保整加成，
    优先输出高价值、高密度条目，并严格截断至 <= max_items。
    """
    if not candidates:
        return []
    if max_items <= 0:
        return []

    scored_candidates = []
    for item in candidates:
        score = float(item.get("score", 0.0))
        layer = item.get("layer")
        if not layer or layer not in ("h0", "h1", "h2"):
            # 若条目未带 layer，尝试从内部 memory 或对象推断
            mem = item.get("memory") or item
            layer = classify_memory_layer(mem)

        mult = LAYER_DENSITY_MULTIPLIER.get(layer, 1.0)
        density_score = score * mult

        # 完璧原子事实加成：具备结构化事实投影的事实拥有更高的信息确定度
        if item.get("atomic_facts"):
            density_score *= 1.1

        scored_candidates.append((density_score, item))

    # 按信息密度得分降序排序
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # 严格截断至 max_items
    return [item for _, item in scored_candidates[:max_items]]
