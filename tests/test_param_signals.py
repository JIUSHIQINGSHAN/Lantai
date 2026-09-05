"""
论文质量信号冒烟测试（方向一）——纯函数真实直调，含真实 arXiv Atom 固件。
"""
from datetime import UTC, datetime, timedelta

import feedparser
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.models.tables import RawDocument
from lantai.parameters.paper_signals import (
    QualitySignalDraft,
    classify_tier,
    classify_venue,
    compute_staleness,
    extract_quality_signals,
)
from lantai.parameters.signal_service import (
    load_signal_views,
    upsert_from_draft,
)
from lantai.parameters.trust_models import PaperQualitySignal

NOW = datetime(2026, 8, 1, tzinfo=UTC)

# 真实 arXiv Atom 固件（含 arxiv:comment / arxiv:journal_ref / arxiv:doi 自定义命名空间）
ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2503.12345v2</id>
    <updated>2025-05-20T00:00:00Z</updated>
    <published>2025-03-21T00:00:00Z</published>
    <title>Hybrid Retrieval Fusion</title>
    <summary>We study weighted fusion of dense and sparse retrieval signals.</summary>
    <author><name>Alice Chen</name></author>
    <arxiv:comment>Accepted at SIGIR 2025</arxiv:comment>
    <arxiv:journal_ref>Proc. SIGIR 2025</arxiv:journal_ref>
    <arxiv:doi>10.1145/0000000.0000000</arxiv:doi>
    <link title="pdf" rel="related" type="application/pdf"
          href="http://arxiv.org/pdf/2503.12345v2"/>
    <arxiv:primary_category term="cs.IR"/>
    <category term="cs.IR"/>
    <category term="cs.CL"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2506.99999v1</id>
    <updated>2026-07-20T00:00:00Z</updated>
    <published>2026-07-20T00:00:00Z</published>
    <title>Fresh Preprint</title>
    <summary>A brand new preprint with no venue info.</summary>
    <arxiv:comment>Submitted to NeurIPS 2026</arxiv:comment>
  </entry>
</feed>
"""


class TestClassifyVenue:
    def test_negative_wins_over_top_conf(self):
        """'Submitted to NeurIPS 2026' 必须判 preprint，绝不能判 top_conf。"""
        d = classify_venue("Submitted to NeurIPS 2026", None, None)
        assert d.venue_class == "preprint"
        assert d.matched_pattern.lower() == "submitted to"

    def test_journal_ref_wins(self):
        d = classify_venue("", "Proc. SIGIR 2025", None)
        assert d.venue_class == "journal"

    def test_doi_is_journal(self):
        d = classify_venue("", None, "10.1145/123.456")
        assert d.venue_class == "journal"

    def test_top_conf_positive(self):
        d = classify_venue("Accepted at NeurIPS 2025", None, None)
        assert d.venue_class == "top_conf"

    def test_workshop(self):
        # 方案规则：top_conf > workshop，含顶会名的文本判 top_conf；
        # 纯 workshop 文本（无 accepted/submitted）判 workshop
        d = classify_venue("ML Memory Workshop 2025", None, None)
        assert d.venue_class == "workshop"

    def test_empty_is_preprint(self):
        assert classify_venue("", None, None).venue_class == "preprint"


class TestClassifyTier:
    def test_journal_is_a(self):
        d = classify_tier(QualitySignalDraft(journal_ref="Proc. SIGIR 2025"),
                          now=NOW)
        assert d.tier == "A"

    def test_top_conf_is_a(self):
        d = classify_tier(QualitySignalDraft(
            comment_raw="Accepted at ICLR 2026"), now=NOW)
        assert d.tier == "A"

    def test_workshop_is_b(self):
        d = classify_tier(QualitySignalDraft(
            comment_raw="Workshop paper"), now=NOW)
        assert d.tier == "B"

    def test_fresh_v1_preprint_is_d(self):
        d = classify_tier(QualitySignalDraft(
            published_at=NOW - timedelta(days=3), version=1), now=NOW)
        assert d.tier == "D"

    def test_v2_fresh_is_c(self):
        d = classify_tier(QualitySignalDraft(
            published_at=NOW - timedelta(days=3), version=2), now=NOW)
        assert d.tier == "C"

    def test_old_v1_is_c(self):
        d = classify_tier(QualitySignalDraft(
            published_at=NOW - timedelta(days=90), version=1),
            now=NOW, seasoned_days=60)
        assert d.tier == "C"

    def test_missing_fields_degrade_to_d(self):
        d = classify_tier(QualitySignalDraft(), now=NOW)
        assert d.tier == "D"


class TestStaleness:
    def test_fresh(self):
        assert compute_staleness(
            NOW - timedelta(days=30), now=NOW).level == "fresh"

    def test_warn(self):
        assert compute_staleness(
            NOW - timedelta(days=30 * 20), now=NOW,
            warn_months=18, block_months=36).level == "warn"

    def test_blocked(self):
        assert compute_staleness(
            NOW - timedelta(days=30 * 40), now=NOW,
            warn_months=18, block_months=36).level == "blocked"


class TestExtractRealAtom:
    def test_extract_from_real_fixture(self):
        feed = feedparser.parse(ATOM_FIXTURE)
        e = feed.entries[0]
        draft = extract_quality_signals(e, fetched_at=NOW)
        assert draft.arxiv_id == "2503.12345"
        assert draft.version == 2
        assert draft.journal_ref == "Proc. SIGIR 2025"
        assert draft.doi.startswith("10.1145")
        assert draft.comment_raw == "Accepted at SIGIR 2025"
        assert draft.primary_category == "cs.IR"
        assert draft.author_count == 1
        assert draft.pdf_url == "http://arxiv.org/pdf/2503.12345v2"
        # 全链路：tier A
        assert classify_tier(draft, now=NOW).tier == "A"

    def test_negative_in_real_fixture(self):
        feed = feedparser.parse(ATOM_FIXTURE)
        draft = extract_quality_signals(feed.entries[1], fetched_at=NOW)
        assert classify_venue(draft.comment_raw, draft.journal_ref,
                              draft.doi).venue_class == "preprint"
        assert classify_tier(draft, now=NOW).tier == "D"


class TestSignalService:
    def test_upsert_and_load(self, param_env):
        session_factory, _ = param_env
        doc = RawDocument(id=new_id("doc"), source_type="paper",
                          source_id="x", url="u", title="t", content="c",
                          content_hash=new_id("h"))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_id = doc.id
        draft = QualitySignalDraft(journal_ref="Proc. SIGIR 2025",
                                   arxiv_id="2503.12345", version=2)
        sig = upsert_from_draft(doc_id, draft, now=NOW)
        assert sig.tier == "A"
        assert sig.signal_source == "arxiv_atom"

        views = load_signal_views([doc_id], now=NOW)
        assert views[doc.id].evidence_tier == "A"
        assert views[doc.id].primary_evidence_eligible is True
        assert views[doc.id].arxiv_id == "2503.12345"

    def test_upsert_idempotent(self, param_env):
        session_factory, _ = param_env
        doc = RawDocument(id=new_id("doc"), source_type="paper",
                          source_id="x", url="u", title="t", content="c",
                          content_hash=new_id("h"))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_id = doc.id
        upsert_from_draft(doc_id, QualitySignalDraft(), now=NOW)
        upsert_from_draft(doc_id, QualitySignalDraft(), now=NOW)
        with session_factory() as s:
            rows = s.exec(select(PaperQualitySignal)).all()
            assert len(rows) == 1

    def test_empty_draft_degrades_to_d(self, param_env):
        session_factory, _ = param_env
        doc = RawDocument(id=new_id("doc"), source_type="paper",
                          source_id="x", url="u", title="t", content="c",
                          content_hash=new_id("h"))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_id = doc.id
        sig = upsert_from_draft(doc_id, QualitySignalDraft(), now=NOW)
        assert sig.tier == "D"
        assert sig.venue_class == "preprint"
        views = load_signal_views([doc_id], now=NOW)
        assert views[doc.id].primary_evidence_eligible is False

    def test_staleness_degrade_warn(self, param_env):
        """warn 降级：A→B，且视图 eligible 仍为 True（blocked 才禁）。"""
        session_factory, _ = param_env
        doc = RawDocument(id=new_id("doc"), source_type="paper",
                          source_id="x", url="u", title="t", content="c",
                          content_hash=new_id("h"))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_id = doc.id
        old = NOW - timedelta(days=30 * 20)  # 20 个月 → warn
        draft = QualitySignalDraft(journal_ref="J", published_at=old)
        sig = upsert_from_draft(doc_id, draft, now=NOW)
        assert sig.tier == "B"  # A 被 warn 降级
        views = load_signal_views([doc_id], now=NOW)
        assert views[doc.id].primary_evidence_eligible is True

    def test_staleness_blocked_disables_eligibility(self, param_env):
        session_factory, _ = param_env
        doc = RawDocument(id=new_id("doc"), source_type="paper",
                          source_id="x", url="u", title="t", content="c",
                          content_hash=new_id("h"))
        with session_factory() as s:
            s.add(doc)
            s.commit()
            doc_id = doc.id
        very_old = NOW - timedelta(days=30 * 40)  # 40 个月 → blocked
        draft = QualitySignalDraft(journal_ref="J", published_at=very_old)
        sig = upsert_from_draft(doc_id, draft, now=NOW)
        assert sig.tier == "D"  # blocked 强制 D
        views = load_signal_views([doc_id], now=NOW)
        assert views[doc.id].primary_evidence_eligible is False
