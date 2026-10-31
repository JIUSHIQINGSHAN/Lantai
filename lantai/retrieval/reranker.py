"""Reranker 客户端：调用硅基流 /v1/rerank 做精排"""

import contextlib
import contextvars
import time
from urllib.parse import urlparse

import requests

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.ingestion.safety import validate_api_url

# SSRF 防线（双保险之二）：host 白名单为模块级固定字面量，
# 与 settings.ALLOWED_API_HOSTS 同口径；不在名单即拒绝出网
_ALLOWED_RERANKER_HOSTS = frozenset({"api.siliconflow.cn", "api.openai.com"})

# 调用方时间预算（time.monotonic() 截止点）。Hermes 注入帧整体只有
# SHELL_HOOK_TIMEOUT（默认 2s），而 RERANKER_TIMEOUT 默认 30s + 失败后 sleep 1s 重试：
# 一次慢请求就让整帧超时，连常驻画像一起丢（2026-10-31 实测：无网时首帧 4.6s、
# 热帧 2.4s，注入均为空）。设了截止点时：单次请求超时取剩余预算，剩余不够
# 「等待 + 再来一次」就不重试，预算耗尽直接返回 [] 走既有降级（按融合分排序）。
# 未设置时行为与改前一字不差（MCP/REST 等无硬预算的调用方不受影响）。
_RERANK_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "lantai_rerank_deadline", default=None
)
_MIN_REQUEST_BUDGET = 0.15  # 秒：剩余预算低于此值不再发请求


@contextlib.contextmanager
def rerank_deadline(seconds: float):
    """在 with 块内给 rerank 设硬预算（相对现在的秒数）。"""
    token = _RERANK_DEADLINE.set(time.monotonic() + max(0.0, float(seconds)))
    try:
        yield
    finally:
        _RERANK_DEADLINE.reset(token)


def _remaining_budget() -> float | None:
    deadline = _RERANK_DEADLINE.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()


def rerank(query: str, documents: list[str], top_k: int) -> list[dict]:
    """对候选文档做重排，返回 (score, document_text) 列表

    失败时重试 1 次，还失败返回空列表（由调用方降级到混合检索结果）
    """
    if not documents:
        return []

    # 审计 M7：base_url host 必须命中 allowlist
    try:
        validate_api_url(settings.RERANKER_BASE_URL)
    except ValueError as e:
        logger.warning("RERANKER_BASE_URL rejected: %s", e)
        return []
    # SSRF 防线：解析后 host 必须命中固定白名单（防 base_url 指向内网/元数据）
    host = (urlparse(settings.RERANKER_BASE_URL).hostname or "").lower()
    if host not in _ALLOWED_RERANKER_HOSTS:
        logger.warning("RERANKER_BASE_URL host not allowed: %s", host)
        return []

    url = f"{settings.RERANKER_BASE_URL}/rerank"
    # 独立最小权限密钥优先，回退 OPENAI_API_KEY（不打印任何密钥）
    api_key = settings.RERANKER_API_KEY or settings.OPENAI_API_KEY
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.RERANKER_MODEL,
        "query": query,
        "documents": documents,
        "top_k": top_k,
        "return_documents": True,
    }

    # 首次 + 重试 1 次（单调用点循环；失败返回空列表由调用方降级）
    for attempt in range(2):
        timeout: float = settings.RERANKER_TIMEOUT
        remaining = _remaining_budget()
        if remaining is not None:
            if remaining < _MIN_REQUEST_BUDGET:
                logger.info("reranker skipped: caller budget exhausted (%.2fs left)", remaining)
                return []
            timeout = min(timeout, remaining)
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return _parse_response(resp.json(), documents, top_k)
        except Exception:
            if attempt == 0:
                delay = settings.RERANKER_RETRY_DELAY
                remaining = _remaining_budget()
                if remaining is not None and remaining < delay + _MIN_REQUEST_BUDGET:
                    return []  # 预算不够等一次再试：直接降级，别把整帧拖超时
                time.sleep(delay)
                continue
            return []


def _parse_response(data: dict, original_docs: list[str], top_k: int) -> list[dict]:
    """解析硅基流 reranker 响应

    审查 M11（票 15）：缺失/越界 index 的返回项跳过并记 warning 留痕，
    不默认 0、不脏配候选；全部缺失时返回空列表，由调用方走既有降级链
    （拾遗 ADR-0028 口径：宁降级不脏写）。
    """
    results = data.get("results", [])
    window = results[:top_k]
    reranked = []
    skipped = 0
    for item in window:
        idx = item.get("index")
        # index 必须是指向 original_docs 的有效整型下标；bool 是 int 子类，
        # 一并排除（防 false→0 重演脏配第 0 号候选）
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(original_docs):
            skipped += 1
            continue
        score = item.get("score", 0.0)
        doc_text = item.get("document", original_docs[idx])
        reranked.append({"score": score, "document": doc_text, "index": idx})
    if skipped:
        logger.warning(
            "Rerank response: skipped %d/%d result(s) with missing or out-of-range index",
            skipped,
            len(window),
        )
    return reranked
