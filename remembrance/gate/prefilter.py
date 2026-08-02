"""
相关性闸门 — 启发式判断查询是否需要记忆检索
参考：aiduMEM ducky/pipeline/memory_gate.py

规则（优先级从高到低）：
1. 纠错/纠偏 → 需要记忆
2. 纯社交结束语 → 不需要
3. 追问热缓存（15秒内） → 沿用上一轮
4. 自我指代/实体 → 需要 Identity
5. 明确回忆请求 → 需要 Episode
6. 指代/延续 → 需要 Episode
7. 有实质内容（>15字+实词）→ 需要 Pinned
8. 默认 → 不需要
"""
import os
import re
import time
import logging

from remembrance.core.settings import settings

logger = logging.getLogger("remembrance.gate")

# ── 正则模式 ──
REFERENCE_PATTERNS = re.compile(
    r'上次|之前|以前|前面|刚刚|刚刚|过去|曾经|还记得|'
    r'上次说的|上回|那.*(事|问题|话题|项目|任务)|'
    r'上次.*(聊|说|讲|提到|讨论)|'
    r'继续|接着|再.*(说|讲|聊)|'
    r'我们.*(决定|说过|定|约)|'
    r'last time|previously|before|earlier|'
    r'remember|recall|what.*(we|I).*said|'
    r'continue|go on|pick up',
    re.IGNORECASE
)

EXPLICIT_RECALL = re.compile(
    r'记得|忘记|忘了|记不|想起来|想不起|回忆|'
    r'查.*记忆|查.*历史|搜索.*记忆|'
    r'remember|forgot|forget|recall|search.*memory',
    re.IGNORECASE
)

# 不需要记忆的纯社交结束语
NO_MEMORY_PATTERNS = re.compile(
    r'^(ok|好|嗯|哦|行|可以|是的|对|收到|了解|明白|知道了|再见|拜拜|谢谢|'
    r'yes|no|yep|nope|k|kk|okay|thanks|bye|got it|sure|alright|'
    r'hello|hi|hey|早上好|晚上好|晚安)[!！。.]{0,3}$',
    re.IGNORECASE
)

# 纠错/纠偏
CORRECTION_PATTERNS = re.compile(
    r'不对|不是这|你记错|错了|no, |wrong|actually|not really|记错了|你说错',
    re.IGNORECASE
)

# 自我指代基础模式
_BASE_SELF_REFERENCE = (
    r'我的|我是|我叫|我.*(名字|生日|年龄|地址|电话|邮箱)|'
    r'assistant|agent|user|用户'
)


def _build_self_reference() -> re.Pattern:
    """构建自我指代正则，支持环境变量注入自定义实体"""
    extra = os.environ.get("ENTITY_KEYWORDS", "").strip().strip("|")
    pattern = _BASE_SELF_REFERENCE
    if extra:
        safe = "|".join(re.escape(w.strip()) for w in extra.split("|") if w.strip())
        if safe:
            pattern = f"{pattern}|{safe}"
    return re.compile(pattern, re.IGNORECASE)


SELF_REFERENCE = _build_self_reference()

# 热缓存
_LAST_GATE_DECISION = {"time": 0.0, "query": "", "needs_memory": False}


def relevance_check(query: str) -> dict:
    """
    判断查询是否需要记忆上下文。
    返回 {"needs_memory": bool, "reason": str, "scope": str}
    """
    if not query or len(query.strip()) < 2:
        return {"needs_memory": False, "reason": "query_too_short", "scope": None}

    q = query.strip()

    # 0. 纠错/纠偏 → 一定需要
    if CORRECTION_PATTERNS.search(q):
        return _update_cache(True, q, "correction_detected", "episode")

    # 1. 纯社交结束语 → 不需要
    if NO_MEMORY_PATTERNS.match(q):
        return _update_cache(False, q, "social_closer", None)

    # 2. 追问热缓存
    now = time.time()
    if (now - _LAST_GATE_DECISION["time"]) < settings.GATE_CACHE_TTL:
        if _LAST_GATE_DECISION["needs_memory"] and len(q) < 12:
            return _update_cache(True, q, "session_followup_hot", "episode")

    # 3. 自我指代
    if SELF_REFERENCE.search(q):
        return _update_cache(True, q, "self_reference", "identity")

    # 4. 明确回忆请求
    if EXPLICIT_RECALL.search(q):
        return _update_cache(True, q, "explicit_recall", "episode")

    # 5. 指代/延续
    if REFERENCE_PATTERNS.search(q):
        return _update_cache(True, q, "reference", "episode")

    # 6. 有实质内容
    if len(q) > 15 and _has_content_words(q):
        return _update_cache(True, q, "content_query", "pinned")

    # 7. 默认不需要
    return _update_cache(False, q, "no_signal", None)


def _update_cache(needs_memory: bool, query: str, reason: str, scope: str) -> dict:
    """更新热缓存并返回结果"""
    global _LAST_GATE_DECISION
    _LAST_GATE_DECISION = {"time": time.time(), "query": query, "needs_memory": needs_memory}
    return {"needs_memory": needs_memory, "reason": reason, "scope": scope}


def _has_content_words(text: str) -> bool:
    """判断文本是否含实质内容（非纯功能词）"""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    if chinese_chars >= 5:
        return True
    content_patterns = [
        r'\b(what|how|why|when|where|who|which|explain|describe|'
        r'analyze|compare|create|build|fix|debug|deploy|install|'
        r'config|setup|migrate|upgrade|error|fail|bug|issue|'
        r'方案|怎么|如何|为什么|帮我|需要|应该|建议|推荐)\b',
    ]
    return any(re.search(pat, text, re.IGNORECASE) for pat in content_patterns)
