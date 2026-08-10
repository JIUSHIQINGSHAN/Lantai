"""
衰减类（decay_class）：procedural 永不衰减 / semantic 慢衰减 / episodic 快衰减

decay_class 与 tier（working/long_term 三级生命周期）正交——不动现有生命周期
与淘汰逻辑，仅补充一个「衰减速率」维度。检索结果附带 decay_class 与
decay_multiplier 供调试。
"""
import re

# 半衰期（天）；None = 永不衰减
DECAY_CLASS_HALFLIFE = {"procedural": None, "semantic": 180.0, "episodic": 30.0}

_PROCEDURAL_HINT = re.compile(r"永远|总是|铁律|必须|禁止|绝不|always|never|must")
_SEMANTIC_HINT = re.compile(r"配置|设置|偏好|习惯|默认|config|preference|default|habit")


def infer_decay_class(title: str, content: str = "", metadata: dict | None = None) -> str:
    """推断衰减类：显式 metadata 优先 → 关键词提示 → 兜底 episodic。"""
    if metadata and metadata.get("decay_class") in DECAY_CLASS_HALFLIFE:
        return metadata["decay_class"]
    probe = f"{title}\n{content}"[:200]
    if _PROCEDURAL_HINT.search(probe):
        return "procedural"
    if _SEMANTIC_HINT.search(probe):
        return "semantic"
    return "episodic"


def decay_multiplier(decay_class: str, age_days: float) -> float:
    """按衰减类计算 0.5^(age/halflife)；procedural 恒 1.0。"""
    hl = DECAY_CLASS_HALFLIFE.get(decay_class, 30.0)
    return 1.0 if hl is None else 0.5 ** (max(0.0, age_days) / hl)
