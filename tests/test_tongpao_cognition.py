"""同袍认知联邦统一编排层的 TDD 冒烟测试。"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lantai.cognition.tongpao import PRIVATE_SCOPE, SHARED_SCOPE, TongpaoEngine
from lantai.core.acl import Principal
from lantai.models.tables import MemoryItem, SkillCrystal, SwarmKnowledgeEvent


def _principal(
    agent_id: str,
    *,
    user_id: str = "user-1",
    tenant_id: str = "tenant-1",
    federation_id: str = "fed-1",
) -> Principal:
    return Principal(
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        federation_id=federation_id,
    )


def test_extract_crystals_groups_repeated_successful_runs_without_llm():
    observations = [
        {
            "skill_name": "release-check",
            "trigger_rule": "发布前",
            "steps": ["跑测试", "查状态"],
            "agent_id": "a",
            "success": True,
        },
        {
            "skill_name": "release-check",
            "trigger_rule": "发布前",
            "steps": ["跑测试", "查状态"],
            "agent_id": "b",
            "success": True,
        },
        {
            "skill_name": "release-check",
            "trigger_rule": "发布前",
            "steps": ["跑测试", "查状态"],
            "agent_id": "a",
            "success": False,
        },
    ]

    drafts = TongpaoEngine.extract_crystals(observations)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.skill_name == "release-check"
    assert draft.steps == ["跑测试", "查状态"]
    assert draft.hit_count == 3
    assert draft.success_count == 2
    assert draft.agent_ids == ["a", "b"]


def test_private_workspace_and_shared_hall_are_tenant_and_agent_isolated(param_env):
    session_factory, _ = param_env
    agent_a = _principal("agent-a")
    agent_b = _principal("agent-b")
    other_tenant = _principal(
        "agent-c", user_id="user-2", tenant_id="tenant-2", federation_id="fed-2"
    )

    with session_factory() as session:
        engine_a = TongpaoEngine(session=session, principal=agent_a)
        private_id = engine_a.store_memory("A 的私有执行草稿")
        shared_id = engine_a.store_memory("联邦公共发布规约", shared_scope=SHARED_SCOPE)

    with session_factory() as session:
        visible_b = TongpaoEngine(session=session, principal=agent_b).list_visible_memories()
        visible_other = TongpaoEngine(
            session=session, principal=other_tenant
        ).list_visible_memories()

    assert {row.id for row in visible_b} == {shared_id}
    assert private_id not in {row.id for row in visible_b}
    assert visible_other == []


def test_same_skill_name_is_independent_between_agents(param_env):
    agent_a = _principal("agent-a")
    agent_b = _principal("agent-b")
    draft = TongpaoEngine.extract_crystals(
        [
            {
                "skill_name": "release-check",
                "trigger_rule": "发布前核验",
                "steps": ["跑测试", "查状态"],
                "agent_id": "agent-a",
                "success": True,
            }
        ]
    )[0]

    crystal_a = TongpaoEngine(principal=agent_a).register_crystal(draft)
    crystal_b = TongpaoEngine(principal=agent_b).register_crystal(draft)

    assert crystal_a != crystal_b


def test_shadow_assessment_promotes_and_distributes_shared_crystal(param_env):
    session_factory, _ = param_env
    principal_a = _principal("agent-a")
    principal_b = _principal("agent-b")
    draft = TongpaoEngine.extract_crystals(
        [
            {
                "skill_name": "safe-release",
                "trigger_rule": "发布前",
                "steps": ["测试", "核验"],
                "agent_id": "agent-a",
                "success": True,
            },
        ]
    )[0]

    with session_factory() as session:
        engine_a = TongpaoEngine(session=session, principal=principal_a)
        crystal_id = engine_a.register_crystal(draft)
        engine_a.record_shadow_result(crystal_id, agent_id="agent-a", success=True)
        with pytest.raises(PermissionError, match="只能登记当前 Principal 的 Agent"):
            engine_a.record_shadow_result(crystal_id, agent_id="agent-b", success=True)
        with pytest.raises(PermissionError, match="无权访问该结晶"):
            TongpaoEngine(session=session, principal=principal_b)._get_crystal(session, crystal_id)

    with session_factory() as session:
        engine_b = TongpaoEngine(session=session, principal=principal_b)
        engine_b.record_shadow_result(crystal_id, agent_id="agent-b", success=True)

    with session_factory() as session:
        engine_a = TongpaoEngine(session=session, principal=principal_a)
        engine_a.record_shadow_result(crystal_id, agent_id="agent-a", success=True)
        evaluation = engine_a.evaluate_crystal(crystal_id, min_success_count=3, min_agents=2)

    assert evaluation["decision"] == "approved"

    result = TongpaoEngine(principal=principal_a).promote_crystal(
        crystal_id, min_success_count=3, min_agents=2
    )
    assert result["status"] == "promoted"
    assert result["shared_scope"] == SHARED_SCOPE

    # Retrying an already completed promotion must reuse its publication event.
    repeated = TongpaoEngine(principal=principal_a).promote_crystal(
        crystal_id, min_success_count=3, min_agents=2
    )
    assert repeated["memory_id"] == result["memory_id"]
    assert repeated["swarm_version"] == result["swarm_version"]

    with session_factory() as session:
        visible_b = TongpaoEngine(session=session, principal=principal_b).list_visible_memories()
        sync = TongpaoEngine(session=session, principal=principal_b).sync_diff(since_version=0)
        events = session.exec(select(SwarmKnowledgeEvent)).all()

    assert len(events) == 1
    assert any(
        row.id == result["memory_id"] and row.shared_scope == SHARED_SCOPE for row in visible_b
    )
    assert any(change["memory_id"] == result["memory_id"] for change in sync["changes"])


def test_register_crystal_does_not_trust_unverified_observation_counts(param_env):
    session_factory, _ = param_env
    principal = _principal("agent-a")
    forged = TongpaoEngine.extract_crystals(
        [
            {
                "skill_name": "forged-release",
                "trigger_rule": "发布前",
                "steps": ["测试", "核验"],
                "agent_id": "agent-a",
                "success": True,
            },
            {
                "skill_name": "forged-release",
                "trigger_rule": "发布前",
                "steps": ["测试", "核验"],
                "agent_id": "agent-b",
                "success": True,
            },
            {
                "skill_name": "forged-release",
                "trigger_rule": "发布前",
                "steps": ["测试", "核验"],
                "agent_id": "agent-b",
                "success": True,
            },
        ]
    )[0]

    with session_factory() as session:
        engine = TongpaoEngine(session=session, principal=principal)
        crystal_id = engine.register_crystal(forged)
        assessment = engine.evaluate_crystal(crystal_id)

    assert assessment["decision"] == "deferred"
    assert assessment["success_count"] == 0
    assert assessment["cross_agent_count"] == 0


def test_sync_diff_never_returns_archived_public_memory(param_env):
    session_factory, _ = param_env
    principal = _principal("agent-a")
    with session_factory() as session:
        archived = MemoryItem(
            id="archived-public",
            content="已归档且不应重新分发的内容",
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            agent_id=principal.agent_id,
            federation_id=principal.federation_id,
            shared_scope=SHARED_SCOPE,
            status="archived",
        )
        session.add(archived)
        session.flush()
        session.add(
            SwarmKnowledgeEvent(
                memory_id=archived.id,
                tenant_id=principal.tenant_id,
                federation_id=principal.federation_id,
            )
        )
        session.commit()
        result = TongpaoEngine(session=session, principal=principal).sync_diff()

    assert result["changes"] == []


def test_private_records_default_to_private_scope(param_env):
    session_factory, _ = param_env
    principal = _principal("agent-a")
    with session_factory() as session:
        engine = TongpaoEngine(session=session, principal=principal)
        memory_id = engine.store_memory("默认私有")
        saved = session.get(MemoryItem, memory_id)
    assert saved is not None
    assert saved.shared_scope == PRIVATE_SCOPE


def test_crystal_promoter_entry_requires_cross_agent_shadow_evidence(param_env):
    """旧晋升入口不得绕过同袍影子考评直接发布。"""
    session_factory, _ = param_env
    principal = _principal("agent-a")
    with session_factory() as session:
        session.add(
            SkillCrystal(
                id="legacy-promote-entry",
                tenant_id=principal.tenant_id,
                federation_id=principal.federation_id,
                user_id=principal.user_id,
                agent_id=principal.agent_id,
                source_agent_id=principal.agent_id,
                skill_name="shadow-gated-skill",
                procedure="- 检查变更\n- 执行验证",
                success_count=3,
                replica_agent_ids=[principal.agent_id],
                shadow_state="observing",
                status="observing",
            )
        )
        session.commit()

    from lantai.evolution.crystal_promoter import promote_crystal

    result = promote_crystal("legacy-promote-entry", principal=principal)

    assert result["ok"] is False
    assert result["status"] == "observing"
    with session_factory() as session:
        crystal = session.get(SkillCrystal, "legacy-promote-entry")
        assert crystal.shadow_state == "observing"
        assert (
            session.exec(select(MemoryItem).where(MemoryItem.shared_scope == SHARED_SCOPE)).all()
            == []
        )


def test_crystal_rest_route_requires_cross_agent_shadow_evidence(param_env):
    """REST 晋升入口使用同一影子考评门槛。"""
    session_factory, _ = param_env
    principal = _principal("agent-a")
    with session_factory() as session:
        session.add(
            SkillCrystal(
                id="rest-promote-entry",
                tenant_id=principal.tenant_id,
                federation_id=principal.federation_id,
                user_id=principal.user_id,
                agent_id=principal.agent_id,
                source_agent_id=principal.agent_id,
                skill_name="rest-shadow-gated-skill",
                procedure="- 核对输入\n- 验证结果",
                success_count=3,
                replica_agent_ids=[principal.agent_id],
                shadow_state="observing",
                status="observing",
            )
        )
        session.commit()

    from lantai.api.routes_crystals import crystal_promote_route
    from lantai.models.schemas import CrystalPromoteReq

    result = crystal_promote_route(
        "rest-promote-entry",
        CrystalPromoteReq(steps=["替换为未考评步骤"]),
        ctx=principal,
    )

    assert result["ok"] is False
    assert result["status"] == "observing"
    with session_factory() as session:
        assert (
            session.exec(select(MemoryItem).where(MemoryItem.shared_scope == SHARED_SCOPE)).all()
            == []
        )
