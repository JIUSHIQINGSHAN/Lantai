"""Fastpath 白名单直写——特定句型绕过 LLM 提取直接写入

三类句型：
- 自我声明：「我叫X」「我是X」
- 偏好表达：「我喜欢X」「我不喜欢X」
- 显式指令：「记住：X」「记一下X」

原则：宁 miss 不脏写（precision ≥ 95%，不设 recall）
"""
import re

# 三类句型正则
_PATTERNS = [
    # 自我声明
    re.compile(r"^(?:我(?:的名字(?:是|叫)?)|我是|我叫)\s*[:：]?\s*(.+)$"),
    # 偏好表达
    re.compile(r"^我(?:喜欢|不喜欢|讨厌|偏爱|偏好)\s*[:：]?\s*(.+)$"),
    # 显式指令
    re.compile(r"^(?:记住|记一下|记一下|备注|记录)\s*[:：]?\s*(.+)$"),
]


def fastpath_check(content: str) -> dict | None:
    """检查内容是否匹配 fastpath 句型。

    返回 None 表示不匹配（走 LLM 提取）。
    返回 dict 表示匹配，包含提取的结构化记忆。
    """
    text = content.strip()
    if len(text) < 3:
        return None

    for pattern in _PATTERNS:
        m = pattern.match(text)
        if m:
            value = m.group(1).strip()
            if not value:
                continue

            # 判断 lane
            if pattern is _PATTERNS[0]:
                lane = "fact"
                summary = f"用户自称：{value}"
            elif pattern is _PATTERNS[1]:
                lane = "preference"
                summary = f"用户偏好：{value}"
            else:
                lane = "general"
                summary = value

            return {
                "fastpath": True,
                "lane": lane,
                "summary": summary,
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": [],
                "extractor_confidence": 1.0,
            }

    return None
