import hashlib
from datetime import UTC, datetime

import feedparser

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.ingestion.base import SourceAdapter
from lantai.ingestion.safety import fetch_with_safety, sanitize_source_url
from lantai.models.tables import RawDocument


class RSSAdapter(SourceAdapter):
    kind = "rss"

    def fetch(self, config: dict) -> list[RawDocument]:
        url = config["url"]
        try:
            raw = fetch_with_safety(url)
        except ValueError as e:
            logger.warning("RSS fetch rejected for %s: %s", url, e)
            return []
        feed = feedparser.parse(raw)
        out = []
        for e in feed.entries:
            content = (e.get("summary") or e.get("description") or "").strip()
            h = hashlib.sha256((e.get("link", "") + content).encode()).hexdigest()
            out.append(
                RawDocument(
                    id=new_id("doc"),
                    source_type="article",
                    source_id=e.get("id", e.get("link", "")),
                    # 票 69：feed 作者完全可控字段，入库前过 scheme 白名单
                    # （不合格式置空 + warning；fetch_with_safety 只管 feed 源地址）
                    url=sanitize_source_url(e.get("link", "")),
                    title=e.get("title", "").strip(),
                    authors=[e.get("author")] if e.get("author") else [],
                    published_at=datetime(*e.published_parsed[:6], tzinfo=UTC)
                    if e.get("published_parsed")
                    else None,
                    lang=config.get("lang", "en"),
                    content_hash=h,
                    content=content,
                )
            )
        return out
