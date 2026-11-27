"""Federation ACL regression coverage for secondary cognition read paths."""

from lantai.cognition.famen import FamenEngine
from lantai.cognition.jiaochou import JiaochouEngine
from lantai.cognition.niyi import NiyiEngine
from lantai.cognition.zuozheng import ZuozhengEngine
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, SkillCrystal
from lantai.services.xuanjing_service import build_xuanjing_topology


def test_secondary_cognition_read_paths_isolate_federations(param_env):
    session_factory, _ = param_env
    now = utcnow()
    with session_factory() as session:
        session.add_all(
            [
                MemoryItem(
                    id="fed-a-private",
                    content="federation alpha private memory repeated words",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-a",
                    federation_id="fed-a",
                    lane="general",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                MemoryItem(
                    id="fed-b-private",
                    content="federation beta private memory repeated words",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-b",
                    federation_id="fed-b",
                    lane="general",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                MemoryItem(
                    id="fed-a-shared",
                    content="federation alpha shared memory",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-a",
                    federation_id="fed-a",
                    shared_scope="swarm_shared",
                    lane="rule",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                MemoryItem(
                    id="fed-b-shared",
                    content="federation beta shared memory",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-b",
                    federation_id="fed-b",
                    shared_scope="swarm_shared",
                    lane="rule",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.add_all(
            [
                SkillCrystal(
                    id="crystal-fed-a",
                    skill_name="alpha-skill",
                    trigger_rule="alpha trigger",
                    procedure="alpha procedure",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-a",
                    federation_id="fed-a",
                    shared_scope="swarm_shared",
                    status="approved",
                ),
                SkillCrystal(
                    id="crystal-fed-b",
                    skill_name="beta-skill",
                    trigger_rule="beta trigger",
                    procedure="beta procedure",
                    user_id="u1",
                    tenant_id="t1",
                    agent_id="agent-b",
                    federation_id="fed-b",
                    shared_scope="swarm_shared",
                    status="approved",
                ),
            ]
        )
        session.commit()

        principal = Principal(
            user_id="u1", tenant_id="t1", agent_id="agent-a", federation_id="fed-a"
        )

        skills = FamenEngine(session=session, principal=principal).list_skills()
        skill_names = {skill["name"] for skill in skills}
        assert "alpha-skill" in skill_names
        assert "beta-skill" not in skill_names

        topology = build_xuanjing_topology(session, principal=principal)
        topology_ids = {node["id"] for node in topology["nodes"]}
        assert "fed-a-shared" in topology_ids
        assert "fed-b-shared" not in topology_ids
        assert "fed-b-private" not in topology_ids

        redundancy = JiaochouEngine(session=session, principal=principal).scan_redundancy(
            limit=20, similarity_threshold=0.1
        )
        redundancy_ids = {
            memory_id
            for pair in redundancy["redundant_pairs"]
            for memory_id in (pair["memory_id_a"], pair["memory_id_b"])
        }
        assert "fed-b-private" not in redundancy_ids
        assert "fed-b-shared" not in redundancy_ids

        assert (
            ZuozhengEngine(session=session, principal=principal).inspect_evidence("fed-b-private")[
                "target_memory_id"
            ]
            is None
        )

        outcome = NiyiEngine(session=session, principal=principal).deliberate_proposal(
            proposal_type="deprecate",
            patch_data={"status": "deprecated"},
            target_memory_id="fed-b-private",
        )
        assert outcome["impacted_edges_count"] == 0
        assert "无权访问" in outcome["deliberation_reason"]
