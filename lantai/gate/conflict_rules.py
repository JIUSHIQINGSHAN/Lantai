"""冲突消解确定性层（P0-2 / ADR-0020）：互斥规则 + 反义词碰撞双通道。

check_rules 为纯函数：互斥规则集（settings.CONFLICT_MUTEX_RULES，pair 内两项互斥，
子串匹配）命中即确定性冲突——零 LLM、可复现、可解释。
check_antonyms 为纯函数：反义词集（settings.CONFLICT_ANTONYM_RULES，jieba 词级匹配）
——词级 token 集合比较，单字反义词（是/不是、会/不会）安全，子串方式会误伤
（"开会" 命中 "会"）。

规则/反义词均未命中才回落 LLM check_contradiction（降级不阻断，见 gate/decision.py）。
账本：命中由调用方在事务内写 ConflictEvent（可溯源、可裁决）。
"""
import jieba

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


def _word_tokens(text: str) -> set[str]:
    """jieba 词级 token 集（含单字词；"开会" 整体一词，不拆出 "会"）。"""
    try:
        return {t for t in jieba.lcut(text) if t.strip()}
    except Exception:  # pragma: no cover - jieba 意外失败退化为整串单 token
        return {text}


def check_antonyms(new_text: str, existing_text: str) -> list[dict]:
    """反义词碰撞：new 词级命中 A 且 existing 命中 B（或反向）→ 冲突。

    词级集合比较——"是" 不因子串命中 "不是"，"会" 不命中 "开会"；
    返回 [{"rule_name", "kind": "antonym", "new_matched", "old_matched"}]。
    """
    if not settings.CONFLICT_ANTONYM_ENABLED:
        return []
    new_toks = _word_tokens(new_text)
    old_toks = _word_tokens(existing_text)
    hits: list[dict] = []
    for rule in settings.CONFLICT_ANTONYM_RULES:
        pair = rule.get("pair") or []
        if len(pair) != 2 or not all(isinstance(x, str) and x for x in pair):
            continue
        a, b = pair
        a_in_new = a in new_toks
        b_in_new = b in new_toks
        a_in_old = a in old_toks
        b_in_old = b in old_toks
        if (a_in_new and b_in_old) or (b_in_new and a_in_old):
            hits.append({
                "rule_name": rule.get("name", "unnamed"),
                "kind": "antonym",
                "new_matched": a if a_in_new else b,
                "old_matched": b if a_in_new else a,
            })
    return hits
