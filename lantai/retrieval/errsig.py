"""errsig（错误签名通道，v022 吸收票据 03，借鉴上游 aiduMEI v21.2 M6）。

报错标识符（CamelCase + Error/Exception/Warning 收尾、errno/错误码）是
「丢了最心疼」的硬事实。本模块是正则**单一真源**：写入侧（gate 抽取）
与检索侧（hybrid 打分 bonus）共用同一份 import——两侧各留拷贝时当下
相等，任一侧演化就会静默错位且不报错（上游自查轮专门收口过）。

纪律：报错名是高辨识度标识符，**只认精确出现**，不做模糊匹配——
模糊化只会放进噪音（兰台「宁可漏不可错」的检索纪律）。
"""

import re

# CamelCase 标识符 + Error/Exception/Warning 收尾；\b 防止子串误命中
_ERRSIG_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,60}(?:Error|Exception|Warning))\b")

# errno / HTTP 状态码风格错误码（写入侧登记用；检索侧不参与打分）
_ERRNO_RE = re.compile(r"\berrno\s*[=:]\s*(\d{1,4})\b|\bE(?:PERM|NOENT|SRCH|INVAL|AGAIN)\b")


def extract_error_signatures(text: str) -> tuple:
    """从文本中取出报错签名（去重保序）。纯函数、零 LLM。

    识别不到返回空元组——绝大多数中文查询走这条，等于整条规则
    不参与打分（零回归）。"""
    if not text:
        return ()
    return tuple(dict.fromkeys(_ERRSIG_RE.findall(text)))


def extract_error_codes(text: str) -> tuple:
    """从文本中取出 errno 风格错误码（写入侧登记用）。"""
    if not text:
        return ()
    return tuple(dict.fromkeys(_ERRNO_RE.findall(text)))


def signature_bonus(content: str, signatures: tuple, bonus: float) -> float:
    """候选正文精确包含查询中的报错签名 → 返回 bonus；否则 0.0。

    bonus 由调用方传入（settings.ERRSIG_BONUS，0 即关闭；调用方负责
    0~1 边界 fail-closed）。"""
    if not signatures or not content or bonus <= 0.0:
        return 0.0
    for sig in signatures:
        if sig in content:
            return bonus
    return 0.0
