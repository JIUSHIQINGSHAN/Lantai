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

v0.4 变更：
- 实体词表惰性编译 + 缓存键校验（修「import 时定死导致环境变量注入
  失效 → 静默零召回」的 bug）；环境变量优先读 REMEMBRANCE_ENTITY_KEYWORDS，
  回退旧名 ENTITY_KEYWORDS 兼容既有部署；未配置时 stderr 告警一次。
- relevance_check 支持注入 cache/now，成为可测的 pure function，同时保持
  _LAST_GATE_DECISION 模块属性以兼容既有测试 fixture。
"""
import logging
import os
import re
import sys
import time

from lantai.core.settings import settings

logger = logging.getLogger("lantai.gate")

# ── 正则模式 ──
REFERENCE_PATTERNS = re.compile(
    r'上次|之前|以前|前面|刚刚|过去|曾经|还记得|'
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
    r'^(?:ok|好|好的|嗯|哦|行|可以|是的|对|收到|了解|明白|知道了|再见|拜拜|谢谢|'
    r'yes|no|yep|nope|k|kk|okay|thanks|bye|got it|sure|alright|'
    r'hello|hi|hey|早上好|晚上好|晚安|[!！。. ]){1,15}$',
    re.IGNORECASE
)

# 纠错/纠偏
CORRECTION_PATTERNS = re.compile(
    r'不对|不是这|你记错|错了|no, |wrong|actually|not really|记错了|你说错',
    re.IGNORECASE
)

# 自我指代基础模式（ADR-0028：收录大哥等项目核心自指）
_BASE_SELF_REFERENCE = (
    r'我的|我是|我叫|我.*(名字|生日|年龄|地址|电话|邮箱)|'
    r'大哥|master|owner|assistant|agent|user|用户'
)

# 技术/领域实体模式（ADR-0028 拾遗：短实词查询放行）
TECHNICAL_DOMAIN_PATTERNS = re.compile(
    r'架构|系统|配置|算法|原理|方案|模型|显卡|接口|数据|微服务|容器|集群|驱动|硬件|依赖',
    re.IGNORECASE
)

# ── 实体词表惰性编译（修 import 时定死）──
_PATTERN_CACHE: dict = {"key": None, "pattern": None}
_KEYWORD_WARNED = False


def _entity_raw() -> str:
    """优先新环境变量名，回退旧名以兼容既有部署。"""
    return (
        os.environ.get("REMEMBRANCE_ENTITY_KEYWORDS")
        or os.environ.get("ENTITY_KEYWORDS", "")
    ).strip().strip("|")


def _self_reference_pattern() -> re.Pattern:
    """惰性编译 + 缓存键校验：环境变量在 import 后设置也生效，零重编译。"""
    global _KEYWORD_WARNED
    raw = _entity_raw()
    if _PATTERN_CACHE["key"] == raw and _PATTERN_CACHE["pattern"] is not None:
        return _PATTERN_CACHE["pattern"]
    if not raw and not _KEYWORD_WARNED:
        _KEYWORD_WARNED = True  # 只吼一次，不静默
        msg = (
            "[lantai.gate] 警告：REMEMBRANCE_ENTITY_KEYWORDS 未配置，"
            "仅识别通用自指模式，专有名词查询可能零召回"
        )
        logger.warning(msg)
        print(msg, file=sys.stderr)
    pattern = _BASE_SELF_REFERENCE
    if raw:
        safe = "|".join(re.escape(w.strip()) for w in raw.split("|") if w.strip())
        if safe:
            pattern = f"{pattern}|{safe}"
    compiled = re.compile(pattern, re.IGNORECASE)
    _PATTERN_CACHE.update(key=raw, pattern=compiled)
    return compiled


# 热缓存（模块级，兼容既有测试 fixture 的 monkeypatch.setattr 替换）
_LAST_GATE_DECISION = {"time": 0.0, "query": "", "needs_memory": False}
_USER_GATE_DECISIONS = {}


def relevance_check(
    query: str, *, user_id: str = "default", cache: dict | None = None, now: float | None = None
) -> dict:
    """
    判断查询是否需要记忆上下文。
    返回 {"needs_memory": bool, "reason": str, "scope": str}

    cache/now 可注入 → 单测无需 monkeypatch 模块属性，并发用例各持独立 cache。
    """
    if cache is None:
        if user_id == "default":
            cache = _LAST_GATE_DECISION
        else:
            cache = _USER_GATE_DECISIONS.setdefault(
                user_id, {"time": 0.0, "query": "", "needs_memory": False}
            )
    
    now = now if now is not None else time.time()

    if not query or len(query.strip()) < 2:
        return {"needs_memory": False, "reason": "query_too_short", "scope": None}

    q = query.strip()

    # 0. 纠错/纠偏 → 一定需要
    if CORRECTION_PATTERNS.search(q):
        return _update_cache(cache, now, True, q, "correction_detected", "episode")

    # 1. 纯社交结束语 → 不需要
    if NO_MEMORY_PATTERNS.match(q):
        return _update_cache(cache, now, False, q, "social_closer", None)

    # 2. 追问热缓存
    if (now - cache["time"]) < settings.GATE_CACHE_TTL:
        if cache["needs_memory"] and len(q) < 12:
            return _update_cache(cache, now, True, q, "session_followup_hot", "episode")

    # 3. 自我指代（惰性编译，环境变量变更即时生效）
    if _self_reference_pattern().search(q):
        return _update_cache(cache, now, True, q, "self_reference", "identity")

    # 4. 明确回忆请求
    if EXPLICIT_RECALL.search(q):
        return _update_cache(cache, now, True, q, "explicit_recall", "episode")

    # 5. 指代/延续
    if REFERENCE_PATTERNS.search(q):
        return _update_cache(cache, now, True, q, "reference", "episode")

    # 6. 有实质内容（长文本或含专业/技术领域实词的短查询，ADR-0028）
    if (len(q) > 15 and _has_content_words(q)) or (len(q) >= 4 and TECHNICAL_DOMAIN_PATTERNS.search(q)):
        return _update_cache(cache, now, True, q, "content_query", "pinned")

    # 7. 默认不需要
    return _update_cache(cache, now, False, q, "no_signal", None)


def _update_cache(
    cache: dict, now: float, needs_memory: bool, query: str, reason: str, scope: str
) -> dict:
    """原地更新热缓存（注入的 cache 或模块级 _LAST_GATE_DECISION）并返回结果。"""
    cache["time"], cache["query"], cache["needs_memory"] = now, query, needs_memory
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
