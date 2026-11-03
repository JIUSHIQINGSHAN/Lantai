"""Reranker 客户端：调用硅基流 /v1/rerank 做精排"""

import time
from urllib.parse import urlparse

import requests

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.ingestion.safety import validate_api_url

# SSRF 防线（双保险之二）：host 白名单为模块级固定字面量，
# 与 settings.ALLOWED_API_HOSTS 同口径；不在名单即拒绝出网
_ALLOWED_RERANKER_HOSTS = frozenset({"api.siliconflow.cn", "api.openai.com"})


def rerank(
    query: str, documents: list[str], top_k: int, timeout: float | None = None
) -> list[dict]:
    """对候选文档做重排，返回 (score, document_text) 列表

    失败时重试 1 次，还失败返回空列表（由调用方降级到混合检索结果）
    timeout（秒）：预算传导（D-04，票 `.scratch/v0.65-next-window-2026-11/01`）
    ——hook 侧传剩余预算，None 用 settings.RERANKER_TIMEOUT。
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
    # X-09 跨服务商密钥防泄漏：仅当未配 RERANKER_API_KEY 且 host 与 OPENAI 同域 (api.openai.com) 时才允许回退
    # 同一服务商的判定按 OPENAI_BASE_URL 的实际 host，而不是写死 api.openai.com：
    # .env.example 推荐的配置就是 OPENAI_BASE_URL=https://api.siliconflow.cn/v1，
    # 此时 OPENAI_API_KEY 本来就是硅基流密钥，回退不外发、也不该把精排静默关掉。
    openai_host = (urlparse(settings.OPENAI_BASE_URL or "").hostname or "").lower()
    api_key = settings.RERANKER_API_KEY
    if not api_key:
        if host == openai_host:
            api_key = settings.OPENAI_API_KEY
        else:
            logger.warning(
                "RERANKER_API_KEY unconfigured for host '%s'; skipping rerank to prevent cross-provider key leakage",
                host,
            )
            return []

    if not api_key:
        logger.warning("No usable API key for reranker; skipping")
        return []

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
    # 预算传导（D-04）：timeout 覆盖 RERANKER_TIMEOUT（hook 传剩余预算）
    eff_timeout = float(timeout) if timeout is not None else settings.RERANKER_TIMEOUT
    # 预算模式只给一次机会：「请求 + sleep(RERANKER_RETRY_DELAY) + 再请求」最坏是
    # 2×timeout+1s，剩余预算 1.6s 时就是 4.2s，必撞 2s 外墙、丢掉整段检索结果。
    attempts = 1 if timeout is not None else 2
    for attempt in range(attempts):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=eff_timeout)
            resp.raise_for_status()
            return _parse_response(resp.json(), documents, top_k)
        except Exception:
            if attempt < attempts - 1:
                time.sleep(settings.RERANKER_RETRY_DELAY)
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
