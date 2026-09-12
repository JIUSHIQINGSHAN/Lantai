from dataclasses import dataclass
from typing import Any

from lantai.models.tables import CognitiveRole, MemoryItem


@dataclass
class CognitivePolicy:
    role: CognitiveRole
    decay_class: str
    base_boost: float
    priority: int  # Higher priority wins conflicts


# Define default policies for each role
_POLICIES = {
    CognitiveRole.OBSERVATION: CognitivePolicy(
        role=CognitiveRole.OBSERVATION, decay_class="episodic", base_boost=1.0, priority=10
    ),
    CognitiveRole.EXPERIENCE: CognitivePolicy(
        role=CognitiveRole.EXPERIENCE, decay_class="episodic", base_boost=1.1, priority=20
    ),
    CognitiveRole.BELIEF: CognitivePolicy(
        role=CognitiveRole.BELIEF, decay_class="semantic", base_boost=1.2, priority=30
    ),
    CognitiveRole.RULE: CognitivePolicy(
        role=CognitiveRole.RULE, decay_class="procedural", base_boost=1.3, priority=40
    ),
    CognitiveRole.PRINCIPLE: CognitivePolicy(
        role=CognitiveRole.PRINCIPLE, decay_class="procedural", base_boost=1.5, priority=50
    ),
    CognitiveRole.SKILL: CognitivePolicy(
        role=CognitiveRole.SKILL, decay_class="procedural", base_boost=1.4, priority=45
    ),
}


def get_cognitive_policy(role: CognitiveRole | str) -> CognitivePolicy:
    """Get the cognitive policy for a given role."""
    if isinstance(role, str):
        try:
            role = CognitiveRole(role)
        except ValueError:
            role = CognitiveRole.OBSERVATION

    return _POLICIES.get(role, _POLICIES[CognitiveRole.OBSERVATION])


def resolve_conflict(item_a: MemoryItem, item_b: MemoryItem) -> MemoryItem:
    """
    Resolve a cognitive conflict between two MemoryItems.
    Returns the item that should win (supersede) the other.

    Resolution logic:
    1. Higher cognitive priority wins (e.g. Principle > Observation).
    2. If priorities are equal, newer wins (created_at).
    """
    policy_a = get_cognitive_policy(item_a.role)
    policy_b = get_cognitive_policy(item_b.role)

    if policy_a.priority > policy_b.priority:
        return item_a
    elif policy_b.priority > policy_a.priority:
        return item_b

    # Priorities are equal, compare recency
    # Assume item_a and item_b have created_at
    time_a = item_a.created_at
    time_b = item_b.created_at

    if time_a >= time_b:
        return item_a
    else:
        return item_b
