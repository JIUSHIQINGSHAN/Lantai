"""Fastpath 白名单直写（向后兼容门面，ADR-0001 / ADR-0064）。

现由直书引擎 `ZhishuEngine`（`lantai.parsing.zhishu`）接管并提供多轨扩展。
本门面保持函数签名与返回值 100% 向后兼容。
"""

from typing import Any

from lantai.parsing.zhishu import ZhishuEngine


def fastpath_check(content: str) -> dict[str, Any] | None:
    """检查内容是否匹配直书/fastpath 确定性句型。

    返回 None 表示不匹配（走 LLM 提取）。
    返回 dict 表示匹配，包含提取的结构化记忆。
    """
    return ZhishuEngine.fastpath_check(content)
