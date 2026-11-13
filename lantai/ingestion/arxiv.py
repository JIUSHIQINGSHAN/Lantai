import hashlib
import ipaddress
import socket
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import feedparser

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.ingestion.base import SourceAdapter
from lantai.ingestion.safety import fetch_with_safety
from lantai.models.tables import RawDocument
from lantai.parameters.paper_signals import extract_quality_signals

# SSRF 防线：arXiv API host 固定字面量白名单（config 只能改查询词，不能改目标）
_ALLOWED_ARXIV_HOSTS = frozenset({"export.arxiv.org"})

# 票 48：max_results 取自 source config（/sources 的 config dict 无 schema
# 约束）——任意大整数 = 每 60 分钟一次的请求放大，钳制 [1, 100]
_ARXIV_MAX_RESULTS_CAP = 100


def _assert_public_arxiv_target(url: str) -> None:
    """协议/host 白名单 + DNS 解析后 IP 边界校验（防内网/元数据地址）。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("arXiv 仅允许 http/https")
    if (parsed.hostname or "").lower() not in _ALLOWED_ARXIV_HOSTS:
        raise ValueError("arXiv 目标 host 不在白名单")
    for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
        parsed.hostname, parsed.port or 80, proto=socket.IPPROTO_TCP
    ):
        ip = ipaddress.ip_address(sockaddr[0])
        # 对齐 safety.validate_fetch_url 的 is_global 参考判据（票 17-2：CGNAT
        # 100.64.0.0/10 曾漏拦——内层松于外层），并叠加原四标志保守面：
        # is_reserved 段（如 NAT64 64:ff9b::/32）不因对齐放开——宁严勿松
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
        ):
            raise ValueError(f"arXiv 目标解析到受限地址 {ip}")


class ArxivAdapter(SourceAdapter):
    kind = "arxiv"

    def fetch(self, config: dict) -> list[RawDocument]:
        query = config.get("query", "cat:cs.AI")
        raw_max = int(config.get("max_results", 10))
        max_results = max(1, min(raw_max, _ARXIV_MAX_RESULTS_CAP))
        if max_results != raw_max:
            logger.warning("arxiv max_results 越界钳制: %s -> %s", raw_max, max_results)
        url = (
            "https://export.arxiv.org/api/query"
            f"?search_query={quote(query)}&start=0&max_results={max_results}"
            "&sortBy=submittedDate&sortOrder=descending"
        )
        _assert_public_arxiv_target(url)
        # 票 48：走 fetch_with_safety 统一防线（SSRF 校验 + 5MB 流式限长 +
        # 超时 + UA），响应体以 **bytes** 喂 feedparser——str 形态会让
        # feedparser 自己用 urllib 发起第二次抓取（无 timeout、无私网校验、
        # file:// 在白名单），构成被劫持态下的二次抓取原语（官方 docstring
        # 明写勿传不可信 str；RSS 链喂 bytes 是仓内正确姿势）
        body = fetch_with_safety(url)
        feed = feedparser.parse(body)
        out: list[RawDocument] = []
        fetched_at = utcnow()
        for e in feed.entries:
            content = (e.get("summary") or "").strip()
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            # 质量信号草稿借道 meta 传递（自由字段，最终落独立表 paper_quality_signal）
            try:
                draft = extract_quality_signals(e, fetched_at=fetched_at)
                signal_payload = draft.model_dump(mode="json")
            except Exception:
                signal_payload = None  # 解析失败 → ingest_worker 落保底 tier D
            out.append(
                RawDocument(
                    id=new_id("doc"),
                    source_type="paper",
                    source_id=e.get("id", ""),
                    url=e.get("link", ""),
                    title=e.get("title", "").strip(),
                    authors=[a.name for a in e.get("authors", [])],
                    published_at=datetime(*e.published_parsed[:6], tzinfo=UTC)
                    if e.get("published_parsed")
                    else None,
                    lang="en",
                    content_hash=h,
                    content=content,
                    meta={"raw": {"arxiv_id": e.get("id")}, "quality_signal": signal_payload},
                )
            )
        return out
