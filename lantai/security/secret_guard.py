"""密钥熔断(票 K 第一道闸):检测疑似密钥形状,命中由调用方转待审队列。

只认完整形状,避免把普通代码误伤成密钥。先影子观察(SECRET_GUARD_MODE=shadow)
后才执法(enforce)。不静默丢弃:命中即交人裁决。

X-06 全链路覆盖（v0.63 票 02）：检测与路由逻辑下沉本模块单一真源——
`detect_secret_shapes` / `route_secret_hits`。调用方（dialogue 摄取、
memory_service 写入漏斗）只做挂载，不各自复制 mode 分支。
默认 shadow：enforce 会把「记住：我的 key 是 sk-...」这类**正当**密钥
记忆强制挂起人工裁决，是假阳性放大器；shadow 检测+记录+不阻断，
与 hook 输出面 `test_no_plaintext_secrets_in_hook_output` 一进一出互补。
"""

from __future__ import annotations

import re
import unicodedata

from lantai.core.logger import logger
from lantai.core.settings import settings

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


def detect_secret_shapes(content: str) -> list[str]:
    """按 SECRET_GUARD_MODE 检测并记录（off 直通不检测；shadow/enforce 命中记 warning）。"""
    if settings.SECRET_GUARD_MODE == "off":
        return []
    hits = find_secret_shapes(content)
    if hits:
        logger.warning("密钥熔断命中(mode=%s): %s", settings.SECRET_GUARD_MODE, hits)
    return hits


def route_secret_hits(result: dict, secret_hits: list[str]) -> dict:
    """enforce 模式下命中候选转待审队列（宁 miss 不脏写，交用户裁决）。

    `result` 须含 `candidate_id`；dedup merge/update 等无新建候选的分支
    返回原样（候选面没新建内容，既有待审语义不变）。
    """
    if not secret_hits or settings.SECRET_GUARD_MODE != "enforce":
        return result
    candidate_id = result.get("candidate_id")
    if not candidate_id:
        return result
    from lantai.services.candidate_service import enqueue_rejected

    enqueue_rejected(candidate_id)
    result["status"] = "pending_review"
    return result
