"""同袍（ADR-0058 · v0.32 Ticket 02）：SkillCrystal 跨 Agent 技能结晶晋升评考与继承冒烟测试。

测试纪律（AGENTS.md）：真实 SQLite + FTS 冒烟，不 mock 核心存储、检索与晋升函数。
仅允许 mock 外部 LLM/向量嵌入依赖。
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.models.tables import MemoryEdge, MemoryItem, SkillCrystal
from lantai.storage.fts import init_fts


@pytest.fixture()
def tongpao_crystal_env():
    """真实纯净 SQLite + FTS 环境，隔离外部向量服务。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
    ):
        yield session_factory, engine


def test_crystal_model_has_federation_fields(tongpao_crystal_env):
    """验证 SkillCrystal 模型已扩展 shared_scope / success_count / source_agent_id 字段。"""
    session_factory, _ = tongpao_crystal_env
    cid = new_id("crystal")
    crystal = SkillCrystal(
        id=cid,
        skill_name="docker-build-best-practice",
        trigger_rule="在构建多阶段 Docker 镜像时触发",
        procedure="1. 使用 Alpine 基础镜像\n2. 剥离调试符号",
        user_id="user_admin",
        agent_id="agent_coder",
        source_agent_id="agent_coder",
        success_count=3,
        shared_scope="private",
    )
    with session_factory() as s:
        s.add(crystal)
        s.commit()

    with session_factory() as s:
        saved = s.get(SkillCrystal, cid)
        assert saved is not None
        assert saved.shared_scope == "private"
        assert saved.success_count == 3
        assert saved.source_agent_id == "agent_coder"


def test_crystal_promotion_evaluation_and_rejection(tongpao_crystal_env):
    """验证技能结晶晋升评考逻辑：不达标（如步骤空/无成功记录）予以拒绝（宁 miss 不脏写）。"""
    session_factory, _ = tongpao_crystal_env
    from lantai.evolution.crystal_promoter import evaluate_crystal_for_promotion

    cid = new_id("crystal")
    # 空 procedure 结晶
    empty_crystal = SkillCrystal(
        id=cid,
        skill_name="empty-skill",
        procedure="",
        agent_id="agent_a",
        success_count=0,
    )
    eligible, reason, _ = evaluate_crystal_for_promotion(empty_crystal, min_success_count=1)
    assert not eligible
    assert "procedure" in reason or "steps" in reason or "success" in reason


def test_crystal_promote_pipeline_to_swarm_shared(tongpao_crystal_env):
    """验证技能结晶成功晋升：
    1. 结晶状态更新为 promoted，shared_scope 标记为 swarm_shared；
    2. 产生公共殿堂 MemoryItem (shared_scope='swarm_shared', memory_type='skill')；
    3. 写入因果/溯源边 MemoryEdge(relation='leads_to')。
    """
    session_factory, _ = tongpao_crystal_env
    from lantai.evolution.crystal_promoter import promote_crystal

    cid = new_id("crystal")
    with session_factory() as s:
        s.add(
            SkillCrystal(
                id=cid,
                skill_name="k8s-rollout-workflow",
                trigger_rule="K8s 生产发布部署",
                procedure="- 步骤一：执行 kubectl rollout status\n- 步骤二：检查健康探针",
                tenant_id="tenant_alpha",
                user_id="user_dev",
                agent_id="agent_devops",
                source_agent_id="agent_devops",
                hit_count=5,
                success_count=3,
                status="candidate",
                shared_scope="private",
            )
        )
        s.commit()

    principal = Principal(tenant_id="tenant_alpha", user_id="user_dev", agent_id="agent_devops")
    res = promote_crystal(
        cid,
        steps=["执行 kubectl rollout status", "检查健康探针", "确认无回滚告警"],
        principal=principal,
    )

    assert res["ok"] is True
    assert res["status"] == "promoted"
    assert res["shared_scope"] == "swarm_shared"
    memory_id = res["memory_id"]
    assert memory_id

    with session_factory() as s:
        # 1. 验证结晶更新
        saved_c = s.get(SkillCrystal, cid)
        assert saved_c.status == "promoted"
        assert saved_c.shared_scope == "swarm_shared"

        # 2. 验证生成的 MemoryItem 进入公共知识殿堂
        mem = s.get(MemoryItem, memory_id)
        assert mem is not None
        assert mem.memory_type == "skill"
        assert mem.shared_scope == "swarm_shared"
        assert mem.tenant_id == "tenant_alpha"

        # 3. 验证图谱溯源边
        edges = s.exec(
            select(MemoryEdge).where(
                MemoryEdge.source_memory_id == cid,
                MemoryEdge.target_memory_id == memory_id,
            )
        ).all()
        assert len(edges) >= 1
        assert edges[0].relation == "leads_to"


def test_cross_agent_skill_inheritance_and_isolation(tongpao_crystal_env):
    """核心验收：跨 Agent 继承共享与私有隔离闭环。
    - Agent A 晋升的技能（swarm_shared）对 Agent B 自动可见并继承；
    - Agent A 未晋升的私有技能，Agent B 无法召回；
    - 异租户 Agent C 即使搜索同一关键词也无法召回（租户隔离保持最高优先级）。
    """
    session_factory, _ = tongpao_crystal_env
    from lantai.evolution.crystal_promoter import promote_crystal
    from lantai.retrieval.hybrid import hybrid_search
    from lantai.services.mem_command import create_skill

    # 1. Agent A 建立一个私有技能（未晋升）
    p_a = Principal(tenant_id="tenant_alpha", user_id="user_dev", agent_id="agent_a")
    priv_skill = create_skill(
        name="agent-a-private-secret",
        description="Agent A 的私有秘籍内部步骤",
        steps=["私有步骤 1", "私有步骤 2"],
        principal=p_a,
    )
    assert priv_skill["ok"] is True

    # 2. Agent A 拥有一个高频结晶并晋升至公共殿堂
    cid = new_id("crystal")
    with session_factory() as s:
        s.add(
            SkillCrystal(
                id=cid,
                skill_name="public-python-packaging",
                trigger_rule="Python 模块分发与打包最佳实践",
                procedure="使用 pyproject.toml 与 hatchling 构建 wheel",
                tenant_id="tenant_alpha",
                user_id="user_dev",
                agent_id="agent_a",
                source_agent_id="agent_a",
                hit_count=6,
                success_count=4,
                status="candidate",
                shared_scope="private",
            )
        )
        s.commit()

    prom_res = promote_crystal(
        cid,
        steps=["配置 pyproject.toml", "运行 hatch build", "检查 dist 产物"],
        principal=p_a,
    )
    assert prom_res["ok"] is True
    public_mem_id = prom_res["memory_id"]

    # 3. Agent B（属于同租户/用户，不同的 agent_id）发起检索
    p_b = Principal(tenant_id="tenant_alpha", user_id="user_dev", agent_id="agent_b")

    # 检索 1：Agent B 搜 Agent A 的私有技能 -> 应当隔离，查不到
    res_priv = hybrid_search("agent-a-private-secret", principal=p_b)
    recalled_priv_ids = [r["memory"]["id"] for r in res_priv]
    assert priv_skill["memory_id"] not in recalled_priv_ids

    # 检索 2：Agent B 搜公共技能 -> 应当成功继承命中！
    res_shared = hybrid_search("public-python-packaging", principal=p_b)
    recalled_shared_ids = [r["memory"]["id"] for r in res_shared]
    assert public_mem_id in recalled_shared_ids

    # 检索 3：异租户 Agent C 发起检索 -> 物理隔离
    p_c = Principal(tenant_id="tenant_other", user_id="user_other", agent_id="agent_c")
    res_other = hybrid_search("public-python-packaging", principal=p_c)
    recalled_other_ids = [r["memory"]["id"] for r in res_other]
    assert public_mem_id not in recalled_other_ids
