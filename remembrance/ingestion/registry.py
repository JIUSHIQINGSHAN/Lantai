from remembrance.ingestion.arxiv import ArxivAdapter
from remembrance.ingestion.rss import RSSAdapter

ADAPTERS = {a.kind: a() for a in [ArxivAdapter, RSSAdapter]}
