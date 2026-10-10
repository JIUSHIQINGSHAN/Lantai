"""密钥熔断(票 K 第一道闸):检测疑似密钥形状,命中由调用方转待审队列。

只认完整形状,避免把普通代码误伤成密钥。先影子观察(SECRET_GUARD_MODE=shadow)
后才执法(enforce)。不静默丢弃:命中即交人裁决。
"""

from __future__ import annotations

import re
import unicodedata

_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"ghp_[A-Za-z0-9]{36}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}


def find_secret_shapes(content: str) -> list[str]:
    """返回命中的密钥形状名称列表;无命中返回空列表。"""
    normalized = unicodedata.normalize("NFKC", content or "")
    return [name for name, pat in _SECRET_PATTERNS.items() if pat.search(normalized)]
