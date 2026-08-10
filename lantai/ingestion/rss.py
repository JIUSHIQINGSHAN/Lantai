import hashlib, feedparser
from datetime import datetime, timezone

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.ingestion.base import SourceAdapter
from lantai.ingestion.safety import fetch_with_safety
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
            out.append(RawDocument(
                id=new_id("doc"),
                source_type="article",
                source_id=e.get("id", e.get("link", "")),
                url=e.get("link", ""),
                title=e.get("title", "").strip(),
                authors=[e.get("author")] if e.get("author") else [],
                published_at=datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                             if e.get("published_parsed") else None,
                lang=config.get("lang", "en"),
                content_hash=h,
                content=content,
            ))
        return out
