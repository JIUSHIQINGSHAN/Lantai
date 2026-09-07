from enum import Enum
from dataclasses import dataclass
from lantai.models.tables import MemoryItem

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
    def _extract_scope(self, item: MemoryItem) -> dict:
        if isinstance(item.structure, dict) and "scope" in item.structure:
            return item.structure["scope"]
        return {}

    def resolve(self, item_a: MemoryItem, item_b: MemoryItem) -> ConflictResult:
        # Check for COEXIST (Different Scopes)
        scope_a = self._extract_scope(item_a)
        scope_b = self._extract_scope(item_b)
        
        if scope_a and scope_b:
            tasks_a = set(scope_a.get("task_types", []))
            tasks_b = set(scope_b.get("task_types", []))
            if tasks_a and tasks_b and tasks_a.isdisjoint(tasks_b):
                return ConflictResult(
                    resolution=ConflictResolution.COEXIST,
                    winner_id=None,
                    reason="Applicable scopes (task_types) are mutually exclusive."
                )
        
        # Calculate scores
        # Simplified for now: just use confidence
        score_a = item_a.confidence
        score_b = item_b.confidence
        
        if score_a > score_b + 0.2:
            return ConflictResult(ConflictResolution.WIN_A, item_a.id, "Item A has significantly higher confidence.")
        elif score_b > score_a + 0.2:
            return ConflictResult(ConflictResolution.WIN_B, item_b.id, "Item B has significantly higher confidence.")
        
        return ConflictResult(ConflictResolution.UNRESOLVED, None, "Scores are too close to determine a winner.")
