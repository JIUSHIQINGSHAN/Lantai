from sqlmodel import Session, select

from lantai.core.ids import new_id
from lantai.models.tables import CognitiveRole, MemoryEdge, MemoryItem


def evaluate_and_evolve(
    session: Session, observations: list[MemoryItem], belief_content: str
) -> MemoryItem:
    """
    Evolve a set of observations into a higher-level belief.
    Creates the Belief memory and the 'supports' edges from observations to the belief.
    """
    # Create the belief memory
    belief = MemoryItem(
        id=new_id("mem"),
        content=belief_content,
        role=CognitiveRole.BELIEF,
        confidence=1.0,  # Initial high confidence
    )
    session.add(belief)

    # Create edges
    for obs in observations:
        edge = MemoryEdge(
            id=new_id("edge"),
            source_memory_id=obs.id,
            target_memory_id=belief.id,
            relation="supports",
            confidence=obs.confidence,
            reason="Evolved from observation",
        )
        session.add(edge)

    session.commit()
    session.refresh(belief)
    return belief


def cascade_decay(session: Session, source_memory_id: str, decay_factor: float = 0.2):
    """
    Cascade confidence decay to parent beliefs when a supporting observation is invalidated or weakened.
    """
    edges = session.exec(
        select(MemoryEdge).where(
            MemoryEdge.source_memory_id == source_memory_id, MemoryEdge.relation == "supports"
        )
    ).all()

    for edge in edges:
        parent = session.get(MemoryItem, edge.target_memory_id)
        if parent:
            # Reduce parent confidence
            parent.confidence = max(0.0, parent.confidence - decay_factor)
            session.add(parent)

            # Reduce edge confidence
            edge.confidence = max(0.0, edge.confidence - decay_factor)
            session.add(edge)

            # Recursively cascade if parent confidence drops below a threshold?
            # For Phase 3 MVP, 1-level is fine, or we can recursively call it.
            # Let's do 1-level for now.

    session.commit()
