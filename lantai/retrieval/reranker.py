"""Reranker 客户端：调用硅基流 /v1/rerank 做精排"""
import time
import requests
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.ingestion.safety import validate_api_url


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

    # 第一次尝试
    try:
        resp = requests.post(url, json=payload, headers=headers,
                            timeout=settings.RERANKER_TIMEOUT)
        resp.raise_for_status()
        return _parse_response(resp.json(), documents, top_k)
    except Exception:
        pass

    # 重试 1 次
    try:
        time.sleep(settings.RERANKER_RETRY_DELAY)
        resp = requests.post(url, json=payload, headers=headers,
                            timeout=settings.RERANKER_TIMEOUT)
        resp.raise_for_status()
        return _parse_response(resp.json(), documents, top_k)
    except Exception:
        return []


def _parse_response(data: dict, original_docs: list[str], top_k: int) -> list[dict]:
    """解析硅基流 reranker 响应"""
    results = data.get("results", [])
    reranked = []
    for item in results[:top_k]:
        idx = item.get("index", 0)
        score = item.get("score", 0.0)
        doc_text = item.get("document", original_docs[idx] if idx < len(original_docs) else "")
        reranked.append({"score": score, "document": doc_text})
    return reranked
