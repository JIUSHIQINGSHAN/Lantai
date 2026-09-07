import pytest
from sqlmodel import select

from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem
from lantai.ops.graph import get_graph
from lantai.retrieval.hybrid import hybrid_search
from lantai.services.memory_service import list_memories


def test_cross_path_isolation(param_env):
    session_factory, _ = param_env

    with session_factory() as s:
        m1 = MemoryItem(
            id="mem_a_1",
            content="Alice secret 1",
            tenant_id="t1",
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            lane="fact",
            domain="user",
            status="active",
        )
        m2 = MemoryItem(
            id="mem_a_2",
            content="Alice secret 2",
            tenant_id="t1",
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            lane="general",
            domain="user",
            status="active",
        )
        m3 = MemoryItem(
            id="mem_b_1",
            content="Bob secret",
            tenant_id="t2",
            user_id="u2",
            agent_id="a2",
            session_id="s2",
            lane="fact",
            domain="user",
            status="active",
        )
        s.add_all([m1, m2, m3])
        s.commit()

        from lantai.retrieval.hybrid import index_memory_item
        from lantai.storage.fts import sync_fts

        sync_fts(s, m1.id, m1.content)
        sync_fts(s, m2.id, m2.content)
        sync_fts(s, m3.id, m3.content)

        index_memory_item(
            m1.id,
            [1.0] * 768,
            {
                "lane": "fact",
                "domain": "user",
                "tenant_id": "t1",
                "user_id": "u1",
                "agent_id": "a1",
                "session_id": "s1",
            },
        )
        index_memory_item(
            m2.id,
            [1.0] * 768,
            {
                "lane": "general",
                "domain": "user",
                "tenant_id": "t1",
                "user_id": "u1",
                "agent_id": "a1",
                "session_id": "s1",
            },
        )
        index_memory_item(
            m3.id,
            [1.0] * 768,
            {
                "lane": "fact",
                "domain": "user",
                "tenant_id": "t2",
                "user_id": "u2",
                "agent_id": "a2",
                "session_id": "s2",
            },
        )

    p_a = Principal(
        tenant_id="t1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        allowed_lanes=["fact", "general"],
        role="user",
    )

    hs_a = hybrid_search("secret", top_k=10, principal=p_a, trace=True)
    print("HS_A:", hs_a)
    assert len(hs_a[0]) == 2
