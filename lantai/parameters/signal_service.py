"""
质量信号 service——写入与读取。

写入：仅 arXiv 适配器经此落库（来源锁断言 signal_source == "arxiv_atom"）。
      解析异常/字段缺失 → 保底 tier D 记录（缺失一律按最低档，绝不能因缺失升档）。
读取：load_signal_views 供建议链路取只读投影。
"""
from datetime import timezone

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import RawDocument
from lantai.parameters.paper_signals import (
    QualitySignalDraft,
    classify_tier,
    classify_venue,
    compute_staleness,
)
from lantai.parameters.trust_models import (
    GatingPolicy,
    PaperQualitySignal,
    QualitySignalView,
)
from lantai.storage import db


def _ensure_aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _apply_staleness_degrade(tier: str, staleness_level: str) -> str:
    """时效降级只降不升：warn 降一级，blocked 强制 D（禁做主证据由 eligible 判定负责）。"""
    if staleness_level == "blocked":
        return "D"
    if staleness_level == "warn":
        return {"A": "B", "B": "C", "C": "D"}.get(tier, tier)
    return tier


def upsert_from_draft(raw_document_id: str,
                      draft: QualitySignalDraft,
                      *, now=None) -> PaperQualitySignal:
    """
    从解析草稿写入/更新质量信号（幂等，raw_document_id 唯一）。
    任何异常 → 保底 tier D 记录，绝不静默跳过也不升级。
    """
    now = now or utcnow()
    try:
        venue = classify_venue(draft.comment_raw, draft.journal_ref, draft.doi)
        tier_dec = classify_tier(draft, now=now,
                                 seasoned_days=settings.PAPER_SEASONED_DAYS)
        stale_dec = compute_staleness(
            _ensure_aware(draft.published_at), now=now,
            warn_months=settings.PAPER_STALE_WARN_MONTHS,
            block_months=settings.PAPER_STALE_BLOCK_MONTHS)
    except Exception as e:  # 解析失败：保底 D，不升级
        logger.warning("quality signal parse failed for %s: %s; fallback tier D",
                       raw_document_id, e)
        venue_class, tier, reason = "unknown", "D", [f"parse_error={e}"]
        stale_level = "fresh"
    else:
        venue_class = venue.venue_class
        tier = _apply_staleness_degrade(tier_dec.tier, stale_dec.level)
        reason = tier_dec.reason + [stale_dec.reason]
        stale_level = stale_dec.level

    with db.get_session() as s:
        existing = s.exec(select(PaperQualitySignal).where(
            PaperQualitySignal.raw_document_id == raw_document_id)).first()
        if existing is None:
            sig = PaperQualitySignal(
                id=new_id("psig"),
                raw_document_id=raw_document_id,
                arxiv_id=draft.arxiv_id,
                version=draft.version,
                published_at=_ensure_aware(draft.published_at),
                updated_at=_ensure_aware(draft.updated_at),
                comment_raw=draft.comment_raw,
                journal_ref=draft.journal_ref,
                doi=draft.doi,
                primary_category=draft.primary_category,
                categories=draft.categories,
                authors=draft.authors,
                author_count=draft.author_count,
                venue_class=venue_class,
                tier=tier,
                tier_reason=reason,
                staleness_level=stale_level,
                signal_source="arxiv_atom",
            )
            s.add(sig)
        else:
            existing.arxiv_id = draft.arxiv_id
            existing.version = draft.version
            existing.published_at = _ensure_aware(draft.published_at)
            existing.updated_at = _ensure_aware(draft.updated_at)
            existing.comment_raw = draft.comment_raw
            existing.journal_ref = draft.journal_ref
            existing.doi = draft.doi
            existing.primary_category = draft.primary_category
            existing.categories = draft.categories
            existing.authors = draft.authors
            existing.author_count = draft.author_count
            existing.venue_class = venue_class
            existing.tier = tier
            existing.tier_reason = reason
            existing.staleness_level = stale_level
            existing.updated_at = utcnow()
            s.add(existing)
        s.commit()
        result = existing or sig
        s.refresh(result)   # commit 后属性已过期，重新加载
        s.expunge(result)   # 脱离 session，属性已加载，调用方可安全读取
        return result


def load_signal_views(raw_document_ids: list[str], *, now=None) -> dict[str, QualitySignalView]:
    """按 raw_document_id 取只读投影（缺失的文档返回空 dict，由调用方按 tier D 处理）。"""
    if not raw_document_ids:
        return {}
    now = now or utcnow()
    with db.get_session() as s:
        rows = s.exec(select(PaperQualitySignal).where(
            PaperQualitySignal.raw_document_id.in_(raw_document_ids))).all()
        docs = s.exec(select(RawDocument).where(
            RawDocument.id.in_(raw_document_ids))).all()
    fetched = {d.id: d.fetched_at for d in docs}
    out: dict[str, QualitySignalView] = {}
    for r in rows:
        pub = _ensure_aware(r.published_at)
        age_days = max(0, (now - pub).days) if pub else 0
        tier = r.tier
        eligible = tier != "D" and r.staleness_level != "blocked"
        out[r.raw_document_id] = QualitySignalView(
            source_id=r.raw_document_id,
            arxiv_id=r.arxiv_id,
            venue_class=r.venue_class,
            evidence_tier=tier,
            published_at=pub,
            version=r.version,
            age_days=age_days,
            staleness_level=r.staleness_level,
            primary_evidence_eligible=eligible,
            tier_reason=list(r.tier_reason),
        )
    return out


def resolve_gating(tier: str, *, staleness_level: str = "fresh",
                   observation_override: int | None = None,
                   venue_class: str | None = None) -> GatingPolicy:
    """tier → 门控策略（权重/互证数/步长预算/观察期）。

    venue_class 提供时叠加方向五可靠性降权（只降不升）；缺省不动（旧调用兼容）。
    """
    weight = settings.TIER_WEIGHT.get(tier, 0.0)
    quorum = settings.QUORUM_BY_TIER.get(tier, 2)
    factor = settings.DELTA_BUDGET_FACTOR.get(tier, 0.5)
    days = observation_override or settings.OBSERVATION_DAYS_BY_TIER.get(tier, 5)
    if venue_class:
        from lantai.parameters.reliability import apply_penalty_to_weight
        weight = apply_penalty_to_weight(weight, venue_class)
    return GatingPolicy(tier=tier, tier_weight=weight,
                        quorum_required=quorum,
                        delta_budget_factor=factor,
                        observation_days=days)
