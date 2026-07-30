import hashlib, feedparser, httpx
from datetime import datetime, timezone

from remembrance.core.ids import new_id
from remembrance.models.tables import RawDocument
from remembrance.ingestion.base import SourceAdapter


class RSSAdapter(SourceAdapter):
    kind = "rss"

    def fetch(self, config: dict) -> list[RawDocument]:
        url = config["url"]
        r = httpx.get(url, timeout=30, follow_redirects=True)
        feed = feedparser.parse(r.text)
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
