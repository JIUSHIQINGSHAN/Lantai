from collections import Counter
from sqlmodel import Session
from lantai.models.tables import MemoryItem, CognitiveRole, CognitivePattern
from lantai.core.ids import new_id
from lantai.core.time import utcnow

class EvolutionEngine:
    def __init__(self, db: Session | None = None):
        self.db = db

    def calculate_promotion_score(
        self, 
        confidence: float, 
        evidence_quality: float, 
        independent_support: float, 
        recurrence: float,
        usefulness: float = 0.5,
        stability: float = 0.5
    ) -> float:
        """
        P = 0.25 × confidence
          + 0.20 × evidence_quality
          + 0.20 × independent_support
          + 0.15 × recurrence
          + 0.10 × usefulness
          + 0.10 × stability
        """
        p = (
            0.25 * confidence +
            0.20 * evidence_quality +
            0.20 * independent_support +
            0.15 * recurrence +
            0.10 * usefulness +
            0.10 * stability
        )
        return p

    def detect_patterns(self, experiences: list[MemoryItem]) -> list[CognitivePattern]:
        """
        对 Experience 列表做语义词袋特征签名聚类。
        提取核心实词集合签名（忽略标点与常见停用虚词），
        当经验在核心实词集合或前缀签名重合 >= 2 次时生成 CognitivePattern。
        """
        import re

        _STOPWORDS = {
            "the", "a", "an", "is", "are", "was", "were", "and", "or", "in", "on", "at",
            "to", "for", "with", "by", "of", "it", "this", "that", "的", "了", "在", "是", "和", "与"
        }

        def _get_signature(text: str) -> tuple[str, str]:
            clean = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text.lower())
            tokens = [w for w in clean.split() if w not in _STOPWORDS and len(w) > 1]
            # 取最显著的核心实词集合（排序保证语序扰动不变）
            core_tokens = sorted(set(tokens))
            sig = " ".join(core_tokens[:4]) if core_tokens else " ".join(clean.split()[:5])
            prefix_display = " ".join(clean.split()[:5])
            return sig, prefix_display or text[:40]

        buckets: dict[str, list[MemoryItem]] = {}
        display_names: dict[str, str] = {}
        for exp in experiences:
            content = exp.content or ""
            sig, display = _get_signature(content)
            buckets.setdefault(sig, []).append(exp)
            if sig not in display_names:
                display_names[sig] = display

        now = utcnow()
        patterns: list[CognitivePattern] = []
        for sig, items in buckets.items():
            if len(items) < 2:
                continue
            occurrence_count = len(items)
            source_ids = [sid for item in items for sid in (item.source_ids or [])]
            independent_source_count = len(set(source_ids))
            confidence = min(0.5 + 0.1 * occurrence_count, 0.9)

            pat = CognitivePattern(
                id=new_id("pat"),
                pattern_type="recurrence",
                description=display_names.get(sig, sig),
                source_ids=source_ids,
                occurrence_count=occurrence_count,
                independent_source_count=independent_source_count,
                confidence=confidence,
                status="candidate",
                created_at=now,
                updated_at=now,
            )
            if self.db is not None:
                self.db.add(pat)
                self.db.commit()
            patterns.append(pat)
        return patterns

    def propose_beliefs(self, patterns: list[CognitivePattern], mock_score: float = 0.0, promotion_threshold: float = 0.70) -> list[MemoryItem]:
        from lantai.models.tables import Evidence

        proposals = []
        for pat in patterns:
            if mock_score:
                score = mock_score
            else:
                # 真实认识论评分：从关联的 source_ids 查询真实 Evidence 表计算证据质量与独立性
                evidence_quality = 0.6
                independent_support = min(1.0, pat.independent_source_count / max(1, pat.occurrence_count))
                recurrence = min(1.0, pat.occurrence_count / 3.0)

                if self.db is not None and pat.source_ids:
                    from sqlmodel import select
                    rows = self.db.exec(
                        select(Evidence).where(Evidence.source_memory_id.in_(pat.source_ids))
                    ).all()
                    if rows:
                        evidence_quality = sum(r.reliability * r.independence for r in rows) / len(rows)
                        independent_support = sum(r.independence for r in rows) / len(rows)

                score = self.calculate_promotion_score(
                    confidence=pat.confidence,
                    evidence_quality=evidence_quality,
                    independent_support=independent_support,
                    recurrence=recurrence,
                )

            if score >= promotion_threshold:
                trace = {
                    "score": round(score, 4),
                    "components": {
                        "confidence": round(pat.confidence, 4),
                        "evidence_quality": round(evidence_quality, 4),
                        "independent_support": round(independent_support, 4),
                        "recurrence": round(recurrence, 4),
                    },
                    "promoted_from": "pattern",
                    "promoted_at": utcnow().isoformat(),
                    "reason": f"{pat.occurrence_count} recurrences, evidence_quality={evidence_quality:.2f}",
                }
                belief = MemoryItem(
                    id=new_id("mem"),
                    content=pat.description,
                    role=CognitiveRole.BELIEF,
                    confidence=score,
                    status="candidate",  # Must be candidate
                    source_ids=pat.source_ids,
                    promotion_trace=trace,
                )
                proposals.append(belief)
        return proposals

    def propose_rules(self, beliefs: list[MemoryItem], mock_score: float = 0.0) -> list[MemoryItem]:
        from sqlmodel import select
        from lantai.models.tables import Evidence

        proposals = []
        for b in beliefs:
            # 默认值（mock_score 路径也能取到）
            evidence_quality = 0.7
            independent_support = 0.7
            usefulness = min(1.0, (b.helpful_count or 0) / max(1, b.use_count or 1))
            if mock_score:
                score = mock_score
            else:
                # 动态根据支撑证据与反思用量计算稳定性与有效性
                if self.db is not None and b.source_ids:
                    rows = self.db.exec(
                        select(Evidence).where(Evidence.source_memory_id.in_(b.source_ids))
                    ).all()
                    if rows:
                        evidence_quality = sum(r.reliability * r.independence for r in rows) / len(rows)
                        independent_support = sum(r.independence for r in rows) / len(rows)

                score = self.calculate_promotion_score(
                    confidence=b.confidence or 0.5,
                    evidence_quality=evidence_quality,
                    independent_support=independent_support,
                    recurrence=0.8,
                    usefulness=usefulness,
                    stability=0.8,
                )

            if score >= 0.80:
                trace = {
                    "score": round(score, 4),
                    "components": {
                        "confidence": round(b.confidence or 0.5, 4),
                        "evidence_quality": round(evidence_quality, 4),
                        "independent_support": round(independent_support, 4),
                        "usefulness": round(usefulness, 4),
                    },
                    "promoted_from": "belief",
                    "promoted_at": utcnow().isoformat(),
                    "reason": f"usefulness={usefulness:.2f}, evidence_quality={evidence_quality:.2f}",
                }
                rule = MemoryItem(
                    id=new_id("mem"),
                    content=b.content,
                    role=CognitiveRole.RULE,
                    confidence=score,
                    status="candidate",
                    source_ids=[b.id] + (b.source_ids or []),
                    promotion_trace=trace,
                )
                proposals.append(rule)
        return proposals

    def propose_principles(self, rules: list[MemoryItem], mock_score: float = 0.0) -> list[MemoryItem]:
        proposals = []
        for r in rules:
            usefulness = min(1.0, (r.helpful_count or 0) / max(1, r.use_count or 1)) if r.use_count else 0.8
            if mock_score:
                score = mock_score
            else:
                score = self.calculate_promotion_score(
                    confidence=r.confidence or 0.5,
                    evidence_quality=0.9,
                    independent_support=0.9,
                    recurrence=0.9,
                    usefulness=usefulness,
                    stability=0.9,
                )

            if score >= 0.90:
                trace = {
                    "score": round(score, 4),
                    "components": {
                        "confidence": round(r.confidence or 0.5, 4),
                        "evidence_quality": 0.9,
                        "independent_support": 0.9,
                        "usefulness": round(usefulness, 4),
                    },
                    "promoted_from": "rule",
                    "promoted_at": utcnow().isoformat(),
                    "reason": f"usefulness={usefulness:.2f}, stable rule promoted to principle",
                }
                prin = MemoryItem(
                    id=new_id("mem"),
                    content=r.content,
                    role=CognitiveRole.PRINCIPLE,
                    confidence=score,
                    status="candidate",
                    source_ids=[r.id] + (r.source_ids or []),
                    structure={
                        "scope": {
                            "domain": "general",
                            "conditions": [],
                            "exceptions": [],
                        }
                    },
                    promotion_trace=trace,
                )
                proposals.append(prin)
        return proposals
