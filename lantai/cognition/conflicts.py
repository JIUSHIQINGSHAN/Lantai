from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lantai.models.tables import MemoryItem

if TYPE_CHECKING:
    from sqlmodel import Session


class ConflictResolution(str, Enum):
    WIN_A = "WIN_A"
    WIN_B = "WIN_B"
    COEXIST = "COEXIST"
    EXCEPTION = "EXCEPTION"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class ConflictResult:
    resolution: ConflictResolution
    winner_id: str | None
    reason: str


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

    def _evidence_stats(self, item: MemoryItem, session: "Session | None") -> tuple[float, float]:
        """Return (evidence_strength, independence_mean) for item from Evidence table.

        evidence_strength = mean(reliability × independence) over all active Evidence rows.
        independence_mean = mean(independence) over same rows.
        Falls back to (0.5, 0.5) when no Evidence found or session is None.
        """
        if session is None:
            return 0.5, 0.5

        from sqlmodel import select
        from lantai.models.tables import Evidence

        rows = session.exec(
            select(Evidence).where(Evidence.source_memory_id == item.id)
        ).all()

        if not rows:
            return 0.5, 0.5

        strength = sum(r.reliability * r.independence for r in rows) / len(rows)
        independence = sum(r.independence for r in rows) / len(rows)
        return strength, independence

    def _score(self, item: MemoryItem, session: "Session | None") -> float:
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

    def resolve(
        self,
        item_a: MemoryItem,
        item_b: MemoryItem,
        session: "Session | None" = None,
    ) -> ConflictResult:
        # --- COEXIST: mutually exclusive task_types (original logic preserved) ---
        scope_a = self._extract_scope(item_a)
        scope_b = self._extract_scope(item_b)

        if scope_a and scope_b:
            tasks_a = set(scope_a.get("task_types", []))
            tasks_b = set(scope_b.get("task_types", []))
            if tasks_a and tasks_b and tasks_a.isdisjoint(tasks_b):
                return ConflictResult(
                    resolution=ConflictResolution.COEXIST,
                    winner_id=None,
                    reason="Applicable scopes (task_types) are mutually exclusive.",
                )

        # --- 6-dim scoring (or simplified confidence-only when no session) ---
        if session is not None:
            score_a = self._score(item_a, session)
            score_b = self._score(item_b, session)
        else:
            # Legacy fast-path: confidence only (backward-compatible)
            score_a = item_a.confidence if item_a.confidence is not None else 0.5
            score_b = item_b.confidence if item_b.confidence is not None else 0.5

        diff = score_a - score_b

        if diff > self._WIN_THRESHOLD:
            return ConflictResult(
                ConflictResolution.WIN_A,
                item_a.id,
                f"Item A score {score_a:.3f} exceeds Item B score {score_b:.3f} by {diff:.3f}.",
            )
        if -diff > self._WIN_THRESHOLD:
            return ConflictResult(
                ConflictResolution.WIN_B,
                item_b.id,
                f"Item B score {score_b:.3f} exceeds Item A score {score_a:.3f} by {-diff:.3f}.",
            )

        return ConflictResult(
            ConflictResolution.UNRESOLVED,
            None,
            f"Scores too close to determine winner (A={score_a:.3f}, B={score_b:.3f}).",
        )
