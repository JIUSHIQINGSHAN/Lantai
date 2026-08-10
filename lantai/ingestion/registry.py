from lantai.ingestion.arxiv import ArxivAdapter
from lantai.ingestion.rss import RSSAdapter

ADAPTERS = {a.kind: a() for a in [ArxivAdapter, RSSAdapter]}
