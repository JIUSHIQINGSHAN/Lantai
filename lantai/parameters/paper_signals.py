"""
论文质量信号——可信度体系 L0 层（方向一）。

所有信号来自 arXiv Atom 结构化字段，纯规则解析，零 LLM 参与。
NEGATIVE_PATTERNS 优先于正向匹配——防止 "Submitted to NeurIPS 2026" 被误判为已接收。
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------- 规则表

VENUE_PATTERNS: dict[str, tuple[str, ...]] = {
    # 顶会正会（白名单，保守匹配，宁可漏判为 B）
    "top_conf": (
        r"\bNeurIPS\b", r"\bICML\b", r"\bICLR\b", r"\bACL\b", r"\bEMNLP\b",
        r"\bNAACL\b", r"\bSIGIR\b", r"\bKDD\b", r"\bWWW\b", r"\bWSDM\b",
        r"\bAAAI\b", r"\bIJCAI\b", r"\bCIKM\b", r"\bCVPR\b", r"\bICCV\b",
    ),
    "other_peer": (
        r"\baccepted\b", r"\bto appear\b", r"\bcamera[- ]ready\b",
        r"\bproceedings of\b",
    ),
    "workshop": (r"\bworkshop\b", r"\bWS\b@"),
}
# 强制判为 preprint 的负面信号（优先级最高）
NEGATIVE_PATTERNS: tuple[str, ...] = (
    r"\bunder review\b", r"\bsubmitted to\b", r"\bpreprint\b",
    r"\bwork in progress\b", r"\btechnical report\b", r"\brejected\b",
)

VenueClass = str  # journal | top_conf | other_peer | workshop | preprint | unknown
Tier = str        # A | B | C | D
StalenessLevel = str  # fresh | warn | blocked


# ---------------------------------------------------------------- 解析模型

class QualitySignalDraft(BaseModel):
    """从 arXiv Atom entry 解析出的结构化信号草稿（纯解析，未分类）。"""
    model_config = ConfigDict(extra="ignore")

    arxiv_id: str = ""
    version: int = 1
    published_at: datetime | None = None
    updated_at: datetime | None = None
    comment_raw: str | None = None
    journal_ref: str | None = None
    doi: str | None = None
    primary_category: str = ""
    categories: list[str] = []
    authors: list[str] = []
    author_count: int = 0
    pdf_url: str | None = None
    abs_url: str | None = None


class VenueDecision(BaseModel):
    venue_class: VenueClass
    matched_pattern: str | None = None   # 命中的原始片段（tier_reason，可审计）


class TierDecision(BaseModel):
    tier: Tier
    reason: list[str]


class StalenessDecision(BaseModel):
    level: StalenessLevel
    reason: str


def _first(entry, *keys):
    for k in keys:
        v = entry.get(k)
        if v:
            return v
    return None


def _arxiv_id_from_entry_id(entry_id: str) -> tuple[str, int]:
    """'http://arxiv.org/abs/2501.00001v2' -> ('2501.00001', 2)。"""
    raw = (entry_id or "").rstrip("/").rsplit("/", 1)[-1]
    m = re.match(r"^(.+?)(?:v(\d+))?$", raw)
    if not m:
        return raw, 1
    base, ver = m.group(1), m.group(2)
    return base, int(ver) if ver else 1


# ---------------------------------------------------------------- 纯解析函数

def extract_quality_signals(entry, *, fetched_at: datetime) -> QualitySignalDraft:
    """
    从 feedparser 解析出的 arXiv Atom entry 提取信号。
    feedparser 对 arxiv namespace 字段的映射同时兼容两种命名：
    `arxiv_comment` / `arxiv_journal_ref` / `arxiv_doi` / `arxiv_primary_category`
    与无前缀版本。纯解析，不调用任何 LLM / 网络。
    """
    entry_id = _first(entry, "id", "link")
    arxiv_id, version = _arxiv_id_from_entry_id(entry_id)
    authors = [a.get("name", "") for a in (entry.get("authors") or [])
               if isinstance(a, dict)]
    links = entry.get("links") or []
    pdf_url = next((l.get("href") for l in links
                    if l.get("type") == "application/pdf"), None)
    abs_url = next((l.get("href") for l in links
                    if l.get("type") == "text/html"), None)

    # feedparser 对 arxiv:primary_category 解析为 {'term': 'cs.IR'} 的 dict
    pc = _first(entry, "arxiv_primary_category", "primary_category")
    if isinstance(pc, dict):
        pc = pc.get("term", "")

    return QualitySignalDraft(
        arxiv_id=arxiv_id,
        version=version,
        published_at=entry.get("published_parsed")
        and datetime(*entry["published_parsed"][:6]),
        updated_at=entry.get("updated_parsed")
        and datetime(*entry["updated_parsed"][:6]),
        comment_raw=_first(entry, "arxiv_comment", "comment"),
        journal_ref=_first(entry, "arxiv_journal_ref", "journal_ref"),
        doi=_first(entry, "arxiv_doi", "doi"),
        primary_category=pc or "",
        categories=[t.get("term", "") for t in (entry.get("tags") or [])
                    if isinstance(t, dict)],
        authors=authors,
        author_count=len(authors),
        pdf_url=pdf_url,
        abs_url=abs_url,
    )


def classify_venue(comment_raw: str | None, journal_ref: str | None,
                   doi: str | None) -> VenueDecision:
    """venue_class 判定。规则优先级：
    journal_ref/doi 非空 > NEGATIVE 命中(强制 preprint) > top_conf > other_peer > workshop > preprint。
    NEGATIVE 优先于正向匹配是唯一红线：'Submitted to ICLR 2026' 必须判 preprint。
    """
    if journal_ref:
        return VenueDecision(venue_class="journal",
                             matched_pattern=journal_ref[:80])
    if doi:
        return VenueDecision(venue_class="journal", matched_pattern=doi[:80])

    text = (comment_raw or "").strip()
    if not text:
        return VenueDecision(venue_class="preprint", matched_pattern=None)

    for pat in NEGATIVE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return VenueDecision(venue_class="preprint",
                                 matched_pattern=m.group(0))

    for vc, pats in VENUE_PATTERNS.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return VenueDecision(venue_class=vc,
                                     matched_pattern=m.group(0))
    return VenueDecision(venue_class="preprint", matched_pattern=None)


def classify_tier(sig: QualitySignalDraft, *, now: datetime,
                  seasoned_days: int = 60) -> TierDecision:
    """
    A/B/C/D 四档：
      A: journal_ref 或 doi 非空，或 venue_class == top_conf
      B: venue_class in {other_peer, workshop}
      C: preprint 且 (version >= 2 或 age_days >= seasoned_days)   # 存活过初筛
      D: 其余（新鲜 v1 纯预印本）
    只降不升：信号缺失一律按最低档处理。
    """
    venue = classify_venue(sig.comment_raw, sig.journal_ref, sig.doi)
    reason = [f"venue={venue.venue_class}"]
    if venue.matched_pattern:
        reason.append(f"match={venue.matched_pattern!r}")

    if venue.venue_class == "journal" or venue.venue_class == "top_conf":
        tier = "A"
    elif venue.venue_class in ("other_peer", "workshop"):
        tier = "B"
    else:  # preprint / unknown
        age_days = 0
        if sig.published_at:
            try:
                pub = sig.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=now.tzinfo)
                age_days = max(0, (now - pub).days)
            except TypeError:
                age_days = 0
        reason.append(f"age_days={age_days} version={sig.version}")
        tier = "C" if sig.version >= 2 or age_days >= seasoned_days else "D"
    return TierDecision(tier=tier, reason=reason)


def compute_staleness(published_at: datetime | None, *, now: datetime,
                      warn_months: int = 18,
                      block_months: int = 36) -> StalenessDecision:
    """按论文发表时间判定时效等级：fresh / warn / blocked。"""
    if published_at is None:
        return StalenessDecision(level="fresh", reason="no_published_at")
    pub = published_at
    if pub.tzinfo is None and now.tzinfo is not None:
        pub = pub.replace(tzinfo=now.tzinfo)
    try:
        months = (now - pub).days / 30.44
    except TypeError:
        return StalenessDecision(level="fresh", reason="naive_utc")
    if months >= block_months:
        return StalenessDecision(
            level="blocked", reason=f"age_months={months:.0f}>={block_months}")
    if months >= warn_months:
        return StalenessDecision(
            level="warn", reason=f"age_months={months:.0f}>={warn_months}")
    return StalenessDecision(level="fresh", reason=f"age_months={months:.0f}")
