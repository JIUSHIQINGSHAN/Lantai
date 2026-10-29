"""直书（Zhishu）服务层——确定性规则匹配与分轨调度 (ADR-0064)。

为 REST 路由、MCP 工具与提取主管线提供无状态、低时延的规则探测与匹配服务。
"""

from __future__ import annotations

from typing import Any

from lantai.parsing.zhishu import ZhishuEngine


def match_zhishu_service(text: str) -> dict[str, Any]:
    """对输入文本执行直书确定性规则匹配分析。

    返回结构：
    - matched: bool 是否匹配到直书规则
    - text: str 原始输入文本
    - rule: dict | None 匹配到的提取规格（若未匹配则为 None）
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {"matched": False, "text": text, "rule": None}

    rule = ZhishuEngine.parse(cleaned)
    return {
        "matched": rule is not None,
        "text": cleaned,
        "rule": rule,
    }
