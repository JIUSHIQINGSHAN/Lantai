from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from lantai.models.tables import MemoryItem

if TYPE_CHECKING:
    from sqlmodel import Session


class ConflictResolution(StrEnum):
    WIN_A = "WIN_A"
    WIN_B = "WIN_B"
    COEXIST = "COEXIST"
    EXCEPTION = "EXCEPTION"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class DecisionTrace:
    """Decision Trace（v0.4）：冲突裁决可解释记录。回答「为什么 A 胜过 B？」"""

    item_a_score: float
    item_b_score: float
    score_delta: float
    score_components_a: dict = field(default_factory=dict)
    score_components_b: dict = field(default_factory=dict)
    decision: str = ""
    reason: str = ""


@dataclass
class ConflictResult:
    resolution: ConflictResolution
    winner_id: str | None
    reason: str
    decision_trace: DecisionTrace | None = None  # v0.4: 可解释裁决追踪


class ConflictEngine:
    # 6-dim weights
    _W_EVIDENCE_STRENGTH = 0.30
    _W_CONFIDENCE = 0.20
    _W_PROVENANCE = 0.15
    _W_RECENCY = 0.15
    _W_INDEPENDENCE = 0.10
    _W_CONTEXTUAL = 0.10

    _WIN_THRESHOLD = 0.15

    def _extract_scope(self, item: MemoryItem) -> dict:
        if isinstance(item.structure, dict) and "scope" in item.structure:
            return item.structure["scope"]
        return {}

    def _evidence_stats(self, item: MemoryItem, session: Session | None) -> tuple[float, float]:
        """Return (evidence_strength, independence_mean) for item from Evidence table.

        evidence_strength = mean(reliability × independence) over all active Evidence rows.
        independence_mean = mean(independence) over same rows.
        Falls back to (0.5, 0.5) when no Evidence found or session is None.
        """
        if session is None:
            return 0.5, 0.5

        from sqlmodel import select

        from lantai.models.tables import Evidence

        rows = session.exec(select(Evidence).where(Evidence.source_memory_id == item.id)).all()

        if not rows:
            return 0.5, 0.5

        strength = sum(r.reliability * r.independence for r in rows) / len(rows)
        independence = sum(r.independence for r in rows) / len(rows)
        return strength, independence

    def _score_with_components(
        self, item: MemoryItem, session: Session | None
    ) -> tuple[float, dict]:
        """Compute 6-dim ConflictScore, return (score, components_dict)."""
        ev_strength, ev_independence = self._evidence_stats(item, session)
        confidence = item.confidence if item.confidence is not None else 0.5
        scope = self._extract_scope(item)
        provenance_quality = 1.0 if scope else 0.5

        if item.created_at is not None:
            created = item.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            days_old = (datetime.now(UTC) - created).days
            recency = max(0.0, 1.0 - days_old / 30.0)
        else:
            recency = 0.5

        contextual_fit = 1.0 if scope.get("domain") else 0.5

        score = (
            self._W_EVIDENCE_STRENGTH * ev_strength
            + self._W_CONFIDENCE * confidence
            + self._W_PROVENANCE * provenance_quality
            + self._W_RECENCY * recency
            + self._W_INDEPENDENCE * ev_independence
            + self._W_CONTEXTUAL * contextual_fit
        )
        components = {
            "evidence_strength": round(ev_strength, 3),
            "confidence": round(confidence, 3),
            "provenance_quality": round(provenance_quality, 3),
            "recency": round(recency, 3),
            "independence": round(ev_independence, 3),
            "contextual_fit": round(contextual_fit, 3),
        }
        return score, components

    def _score(self, item: MemoryItem, session: Session | None) -> float:
        """Compute 6-dimensional ConflictScore for *item*."""
        # --- evidence_strength & independence (from DB) ---
        ev_strength, ev_independence = self._evidence_stats(item, session)

        # --- confidence ---
        confidence = item.confidence if item.confidence is not None else 0.5

        # --- provenance_quality ---
        scope = self._extract_scope(item)
        provenance_quality = 1.0 if scope else 0.5

        # --- recency ---
        if item.created_at is not None:
            created = item.created_at
            # make aware if naive
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            days_old = (datetime.now(UTC) - created).days
            recency = max(0.0, 1.0 - days_old / 30.0)
        else:
            recency = 0.5

        # --- contextual_fit ---
        contextual_fit = 1.0 if scope.get("domain") else 0.5

        score = (
            self._W_EVIDENCE_STRENGTH * ev_strength
            + self._W_CONFIDENCE * confidence
            + self._W_PROVENANCE * provenance_quality
            + self._W_RECENCY * recency
            + self._W_INDEPENDENCE * ev_independence
            + self._W_CONTEXTUAL * contextual_fit
        )
        return score

    def _generate_reason(
        self,
        score_a: float,
        score_b: float,
        comps_a: dict,
        comps_b: dict,
        resolution: ConflictResolution,
    ) -> str:
        """生成自然语言裁决理由（模板化，无 LLM）。"""
        if resolution == ConflictResolution.COEXIST:
            return "两者适用范围互斥（task_types 不相交），可共存。"
        if resolution == ConflictResolution.UNRESOLVED:
            return (
                f"评分差距过小无法裁决（A={score_a:.3f}, B={score_b:.3f}）。"
                "建议人工审查或引入更多 Evidence。"
            )
        winner_label = "A" if resolution == ConflictResolution.WIN_A else "B"
        winner_comps = comps_a if winner_label == "A" else comps_b
        loser_comps = comps_b if winner_label == "A" else comps_a
        loser_label = "B" if winner_label == "A" else "A"
        diffs = {k: round(winner_comps.get(k, 0) - loser_comps.get(k, 0), 3) for k in winner_comps}
        top_dim, top_diff = max(diffs.items(), key=lambda kv: kv[1])
        dim_labels = {
            "evidence_strength": "证据强度",
            "confidence": "置信度",
            "provenance_quality": "溯源质量",
            "recency": "时效性",
            "independence": "证据独立性",
            "contextual_fit": "上下文契合度",
        }
        dim_name = dim_labels.get(top_dim, top_dim)
        w_score = score_a if winner_label == "A" else score_b
        l_score = score_b if winner_label == "A" else score_a
        parts = [
            f"{winner_label} 胜出（综合评分 {w_score:.3f} vs {l_score:.3f}）。",
            f"主要优势在「{dim_name}」"
            f"（{winner_label}={winner_comps[top_dim]:.3f} vs "
            f"{loser_label}={loser_comps.get(top_dim, 0):.3f}，差距 +{top_diff:.3f}）。",
        ]
        secondary = [
            (k, v)
            for k, v in sorted(diffs.items(), key=lambda x: -x[1])
            if v > 0.05 and k != top_dim
        ][:2]
        for dim, diff in secondary:
            parts.append(f"「{dim_labels.get(dim, dim)}」也有优势（+{diff:.3f}）。")
        return " ".join(parts)

    def resolve(
        self,
        item_a: MemoryItem,
        item_b: MemoryItem,
        session: Session | None = None,
    ) -> ConflictResult:
        # --- COEXIST: mutually exclusive task_types (original logic preserved) ---
        scope_a = self._extract_scope(item_a)
        scope_b = self._extract_scope(item_b)

        if scope_a and scope_b:
            tasks_a = set(scope_a.get("task_types", []))
            tasks_b = set(scope_b.get("task_types", []))
            if tasks_a and tasks_b and tasks_a.isdisjoint(tasks_b):
                trace = DecisionTrace(
                    item_a_score=0.0,
                    item_b_score=0.0,
                    score_delta=0.0,
                    decision="COEXIST",
                    reason="两者适用范围互斥（task_types 不相交），可共存。",
                )
                return ConflictResult(
                    resolution=ConflictResolution.COEXIST,
                    winner_id=None,
                    reason=trace.reason,
                    decision_trace=trace,
                )

        # --- 6-dim scoring (or simplified confidence-only when no session) ---
        if session is not None:
            score_a, comps_a = self._score_with_components(item_a, session)
            score_b, comps_b = self._score_with_components(item_b, session)
        else:
            score_a = item_a.confidence if item_a.confidence is not None else 0.5
            score_b = item_b.confidence if item_b.confidence is not None else 0.5
            comps_a = {"confidence": round(score_a, 3)}
            comps_b = {"confidence": round(score_b, 3)}

        diff = score_a - score_b

        if diff > self._WIN_THRESHOLD:
            resolution = ConflictResolution.WIN_A
            winner_id = item_a.id
        elif -diff > self._WIN_THRESHOLD:
            resolution = ConflictResolution.WIN_B
            winner_id = item_b.id
        else:
            resolution = ConflictResolution.UNRESOLVED
            winner_id = None

        reason = self._generate_reason(score_a, score_b, comps_a, comps_b, resolution)
        trace = DecisionTrace(
            item_a_score=round(score_a, 4),
            item_b_score=round(score_b, 4),
            score_delta=round(diff, 4),
            score_components_a=comps_a,
            score_components_b=comps_b,
            decision=resolution.value,
            reason=reason,
        )
        return ConflictResult(
            resolution=resolution,
            winner_id=winner_id,
            reason=reason,
            decision_trace=trace,
        )
