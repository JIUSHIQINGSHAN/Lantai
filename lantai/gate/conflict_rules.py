"""冲突消解确定性层（P0-2）：规则层 + LLM 层双通道。

check_rules 为纯函数：互斥规则集（settings.CONFLICT_MUTEX_RULES，pair 内两项互斥）
命中即确定性冲突——零 LLM、可复现、可解释；规则未命中才回落 LLM check_contradiction
（降级不阻断，见 gate/decision.py）。

账本：规则命中由调用方在事务内写 ConflictEvent（可溯源、可裁决）。
"""
from lantai.core.settings import settings


def check_rules(new_text: str, existing_text: str) -> list[dict]:
    """互斥规则匹配：new 命中 pair 的 A 且 existing 命中 B（或反向）→ 冲突。

    返回 [{"rule_name", "kind": "mutex", "new_matched", "old_matched"}]；
    未命中 / 开关关闭返回 []。
    """
    if not settings.CONFLICT_RULES_ENABLED:
        return []
    hits: list[dict] = []
    for rule in settings.CONFLICT_MUTEX_RULES:
        pair = rule.get("pair") or []
        if len(pair) != 2 or not all(isinstance(x, str) and x for x in pair):
            continue
        a, b = pair
        a_in_new = a in new_text
        b_in_new = b in new_text
        a_in_old = a in existing_text
        b_in_old = b in existing_text
        if (a_in_new and b_in_old) or (b_in_new and a_in_old):
            hits.append({
                "rule_name": rule.get("name", "unnamed"),
                "kind": "mutex",
                "new_matched": a if a_in_new else b,
                "old_matched": b if a_in_new else a,
            })
    return hits
