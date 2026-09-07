import pytest
try:
    from lantai.cognition.conflicts import ConflictEngine, ConflictResult, ConflictResolution
    from lantai.models.tables import MemoryItem, CognitiveRole
except ImportError:
    ConflictEngine = None
    ConflictResult = None
    ConflictResolution = None
    MemoryItem = None
    CognitiveRole = None

def test_conflict_engine_coexist():
    """Verify ConflictEngine returns COEXIST when scopes differ."""
    assert ConflictEngine is not None
    
    # Principle 1 applies to task_types: small_project
    p1 = MemoryItem(
        id="p1", 
        role=CognitiveRole.PRINCIPLE,
        structure={"scope": {"task_types": ["small_project"]}}
    )
    
    # Principle 2 applies to task_types: enterprise_app
    p2 = MemoryItem(
        id="p2", 
        role=CognitiveRole.PRINCIPLE,
        structure={"scope": {"task_types": ["enterprise_app"]}}
    )
    
    engine = ConflictEngine()
    result = engine.resolve(p1, p2)
    
    assert result.resolution == ConflictResolution.COEXIST
    assert "scope" in result.reason.lower()

def test_conflict_engine_win_a():
    """Verify ConflictEngine resolves by evidence strength/confidence."""
    assert ConflictEngine is not None
    
    # Rule 1 has high confidence
    r1 = MemoryItem(id="r1", role=CognitiveRole.RULE, confidence=0.95, structure={})
    
    # Rule 2 has low confidence
    r2 = MemoryItem(id="r2", role=CognitiveRole.RULE, confidence=0.40, structure={})
    
    engine = ConflictEngine()
    result = engine.resolve(r1, r2)
    
    assert result.resolution == ConflictResolution.WIN_A
    assert result.winner_id == r1.id
