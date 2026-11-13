import hashlib
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from lantai.core.ids import new_id
from lantai.core.logger import loggable, logger
from lantai.ingestion.base import SourceAdapter
from lantai.ingestion.safety import fetch_with_safety, sanitize_source_url
from lantai.models.tables import RawDocument


def _strip_html(raw: str) -> str:
    """RSS 摘要正文剥 HTML 标签（票 49）。

    feedparser 内建 sanitize 只清 script/style/iframe 等危险元素，`<p>/<a>`
    等普通标签原样随 summary 进库，经候选/FTS 一路污染到宿主注入帧。BS4
    get_text 一步完成剥标签 + 实体解码（&amp; → &）；块级标签间补空格防
    词语粘连。纯文本摘要（arxiv）原样通过零改写。声明依赖 bs4/lxml
    （pyproject），零新增。
    """
    if not raw:
        return raw
    return BeautifulSoup(raw, "lxml").get_text(separator=" ").strip()


class RSSAdapter(SourceAdapter):
    kind = "rss"

    def fetch(self, config: dict) -> list[RawDocument]:
        url = config["url"]
        try:
            raw = fetch_with_safety(url)
        except ValueError as e:
            # 票 50：URL 原串（含 ?token= 凭证参数/内嵌换行）不进日志
            logger.warning(
                "RSS fetch rejected for %s: %s", loggable(url, limit=200, keep_tail=True), e
            )
            return []
        feed = feedparser.parse(raw)
        out = []
        for e in feed.entries:
            content = _strip_html((e.get("summary") or e.get("description") or "").strip())
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
