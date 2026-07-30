import hashlib
import httpx
import feedparser
from datetime import datetime, timezone

from remembrance.core.ids import new_id
from remembrance.models.tables import RawDocument
from remembrance.ingestion.base import SourceAdapter


class ArxivAdapter(SourceAdapter):
    kind = "arxiv"

    def fetch(self, config: dict) -> list[RawDocument]:
        query = config.get("query", "cat:cs.AI")
        max_results = int(config.get("max_results", 10))
        url = ("http://export.arxiv.org/api/query"
               f"?search_query={query}&start=0&max_results={max_results}"
               "&sortBy=submittedDate&sortOrder=descending")
        r = httpx.get(url, timeout=30)
        feed = feedparser.parse(r.text)
        out: list[RawDocument] = []
        for e in feed.entries:
            content = (e.get("summary") or "").strip()
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            out.append(RawDocument(
                id=new_id("doc"),
                source_type="paper",
                source_id=e.get("id", ""),
                url=e.get("link", ""),
                title=e.get("title", "").strip(),
                authors=[a.name for a in e.get("authors", [])],
                published_at=datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                             if e.get("published_parsed") else None,
                lang="en",
                content_hash=h,
                content=content,
                meta={"raw": {"arxiv_id": e.get("id")}},
            ))
        return out
