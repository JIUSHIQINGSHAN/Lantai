import pytest
from sqlmodel import select, Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from api_server import app
from lantai.core.ids import new_id
from lantai.models.tables import RawDocument, DocumentChunk, MemoryCandidate, MemoryEdge, MemoryItem
import lantai.storage.db as db_module

@pytest.fixture
def mem_db():
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    
    from lantai.storage.fts import init_fts
    init_fts(test_engine.raw_connection())
    
    def get_session_override():
        with Session(test_engine) as session:
            yield session
            
    db_module.engine = test_engine
    
    def session_factory():
        return Session(test_engine)
        
    return session_factory, test_engine

@pytest.fixture
def client(mem_db):
    from lantai.core.auth import get_current_user, SecurityContext
    app.dependency_overrides[get_current_user] = lambda: SecurityContext(user_id="test", allowed_lanes=[])
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_document_cascade_delete(mem_db, client, monkeypatch):
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: type("MockVS", (), {"delete": lambda self, ids: None})())
    session_factory, test_engine = mem_db
    
    # Setup data
    doc_id = new_id("doc")
    cand_id = new_id("cand")
    mem_id = new_id("mem")
    
    with session_factory() as s:
        # 1. RawDocument
        doc = RawDocument(
            id=doc_id, source_type="web", source_id="src1", url="", title="Test Doc", content_hash="hash1", content="Content"
        )
        s.add(doc)
        
        # 2. DocumentChunk
        chunk = DocumentChunk(id=new_id("chunk"), document_id=doc_id, chunk_index=0, text="Content")
        s.add(chunk)
        
        # 3. MemoryCandidate
        cand = MemoryCandidate(id=cand_id, document_id=doc_id, summary="sum")
        s.add(cand)
        
        # 4. MemoryItem
        mem = MemoryItem(id=mem_id, content="memory content")
        s.add(mem)
        
        # 5. MemoryEdge (linking doc to mem)
        edge = MemoryEdge(id=new_id("edge"), source_memory_id=doc_id, target_memory_id=mem_id, relation="extract")
        s.add(edge)
        
        s.commit()
        
    # Verify data exists
    with session_factory() as s:
        assert s.get(RawDocument, doc_id) is not None
        assert s.get(MemoryItem, mem_id) is not None
        assert len(s.exec(select(MemoryEdge)).all()) == 1
        
    # Run API
    resp = client.delete(f"/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["deleted_document_id"] == doc_id
    assert resp.json()["deleted_memories"] == 1
    
    # Verify deletion
    with session_factory() as s:
        assert s.get(RawDocument, doc_id) is None
        assert len(s.exec(select(DocumentChunk)).all()) == 0
        assert len(s.exec(select(MemoryCandidate)).all()) == 0
        assert len(s.exec(select(MemoryEdge)).all()) == 0
        assert s.get(MemoryItem, mem_id) is None
        
def test_document_cascade_delete_partial_memory(mem_db, client):
    """If a memory has edges from multiple docs, it should NOT be deleted, only the edge."""
    session_factory, _ = mem_db
    
    doc1_id = "doc_1"
    doc2_id = "doc_2"
    mem_id = "mem_shared"
    
    with session_factory() as s:
        s.add(RawDocument(id=doc1_id, source_type="test", source_id="s1", url="", title="d1", content_hash="h1"))
        s.add(RawDocument(id=doc2_id, source_type="test", source_id="s2", url="", title="d2", content_hash="h2"))
        s.add(MemoryItem(id=mem_id, content="shared content"))
        s.add(MemoryEdge(id="e1", source_memory_id=doc1_id, target_memory_id=mem_id, relation="extract"))
        s.add(MemoryEdge(id="e2", source_memory_id=doc2_id, target_memory_id=mem_id, relation="extract"))
        s.commit()
        
    # Delete doc 1
    resp = client.delete(f"/documents/{doc1_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted_memories"] == 0  # Should be 0 since it has doc2 edge left
    
    with session_factory() as s:
        assert s.get(RawDocument, doc1_id) is None
        assert s.get(RawDocument, doc2_id) is not None
        assert s.get(MemoryItem, mem_id) is not None # Memory is kept!
        edges = s.exec(select(MemoryEdge)).all()
        assert len(edges) == 1
        assert edges[0].source_memory_id == doc2_id
