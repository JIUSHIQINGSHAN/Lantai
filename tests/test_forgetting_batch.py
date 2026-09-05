"""Ticket 2.4 [DD-06]: Ebbinghaus forgetting (`forgetting.py`) must skip updates if `|Δdecay| < 0.001` and use batching."""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.memory.forgetting import apply_forgetting
from lantai.models.tables import MemoryItem


@pytest.fixture
def fq_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    
    def session_factory() -> Session:
        return Session(engine)
        
    with patch.object(db_module, "get_session", session_factory):
        yield session_factory

def test_forgetting_skips_small_deltas(fq_env):
    session_factory = fq_env
    
    with session_factory() as s:
        m1 = MemoryItem(id="mem1", content="内容", decay_score=1.0, status="active")
        s.add(m1)
        s.commit()

    with patch("lantai.memory.forgetting.utcnow") as mock_utcnow:
        with session_factory() as s:
            m = s.get(MemoryItem, "mem1")
            mock_utcnow.return_value = m.created_at
            
        s = session_factory()
        with patch.object(s, "add") as mock_add, patch.object(s, "commit"):
            with patch("lantai.memory.forgetting.db.get_session", return_value=s):
                apply_forgetting()
                assert mock_add.call_count == 0  # no updates
                    
def test_forgetting_batching(fq_env):
    session_factory = fq_env
    
    with session_factory() as s:
        for i in range(250):
            s.add(MemoryItem(id=f"mem{i}", content=f"内容{i}", decay_score=1.0, status="active"))
        s.commit()
        
    import datetime
    with patch("lantai.memory.forgetting.utcnow") as mock_utcnow:
        with session_factory() as s:
            m = s.get(MemoryItem, "mem0")
            # move time by 100 days so it decays a lot
            mock_utcnow.return_value = m.created_at + datetime.timedelta(days=100)
            
        s = session_factory()
        with patch.object(s, "add"), patch.object(s, "commit") as mock_commit:
            with patch("lantai.memory.forgetting.db.get_session", return_value=s):
                apply_forgetting()
                # If batch size is 100, we should see 3 commits (100, 100, 50)
                assert mock_commit.call_count >= 3
