"""探颐·主动求证探针认知引擎冒烟测试 (Ticket 01)

基于真实 in-memory SQLite，不 mock 内部计算逻辑。
验证接缝（Seams）：
1. TanyiProbingEngine 基于用户 query、召回记忆与未决冲突生成 ProbeInsertion；
2. 针对低置信度记忆（0.3 <= confidence <= 0.6）与未决冲突（ConflictEvent）的多维度插桩打标与建议动作；
3. format_context 生成 Prompt 注入上下文（【探颐·主动求证插桩】）；
4. 多租户与属主安全隔离：非管理员绝不向跨租户/跨用户暴露不属于自身的探针。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition.tanyi import ProbeInsertion, TanyiProbingEngine
from lantai.core.auth import Principal
from lantai.models.tables import ConflictEvent, MemoryItem


@pytest.fixture()
def tanyi_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    return session_factory


def test_tanyi_probing_engine_conflict_and_low_confidence(tanyi_env):
    """Seam 1 & 2: 验证引擎从召回记忆与未决冲突中检出 ProbeInsertion。"""
    session_factory = tanyi_env
    with session_factory() as s:
        # 1. 创建一条冲突记忆与未决冲突
        m1 = MemoryItem(
            id="mem-loc-1",
            content="团队核心常驻办公地点在上海张江",
            confidence=0.9,
            lane="fact",
            tenant_id="t-1",
            user_id="user-1",
            status="active",
        )
        conf1 = ConflictEvent(
            id="conf-loc-1",
            memory_id="mem-loc-1",
            incoming_ref="团队近期已搬迁至北京中关村办公",
            rule_name="location_conflict",
            status="open",
        )
        # 2. 创建一条低置信度事实记忆 (0.45)
        m2 = MemoryItem(
            id="mem-fuzzy-1",
            content="用户偏好喝冷萃不加糖咖啡",
            confidence=0.45,
            lane="preference",
            tenant_id="t-1",
            user_id="user-1",
            status="active",
        )
        s.add(m1)
        s.add(conf1)
        s.add(m2)
        s.commit()

        engine = TanyiProbingEngine()
        principal = Principal(user_id="user-1", tenant_id="t-1", role="user")

        probes = engine.generate_probes(
            query="团队办公地点在哪里，平时喝什么咖啡？",
            recalled_memories=[m1, m2],
            unresolved_conflicts=[conf1],
            session=s,
            principal=principal,
            limit=5,
        )

        assert len(probes) >= 2
        probe_map = {p.target_id: p for p in probes}

        # 验证冲突探针
        assert "mem-loc-1" in probe_map
        p_conf = probe_map["mem-loc-1"]
        assert isinstance(p_conf, ProbeInsertion)
        assert p_conf.conflict_id == "conf-loc-1"
        assert p_conf.suggested_action == "resolve_conflict"
        assert "北京中关村" in p_conf.question or "上海张江" in p_conf.question

        # 验证低置信度探针
        assert "mem-fuzzy-1" in probe_map
        p_fuzzy = probe_map["mem-fuzzy-1"]
        assert p_fuzzy.suggested_action in ("clarify_fact", "verify_low_confidence")
        assert p_fuzzy.confidence == 0.45
        assert "冷萃" in p_fuzzy.question

        # 验证上下文格式化
        prompt_block = engine.format_context(probes)
        assert "【探颐·待求证事项】" in prompt_block or "【探颐·主动求证插桩】" in prompt_block
        assert p_conf.question in prompt_block
        assert p_fuzzy.question in prompt_block


def test_tanyi_probing_tenant_and_acl_isolation(tanyi_env):
    """Seam 4: 验证跨租户与跨用户强隔离，绝不越权泄露他人探针。"""
    session_factory = tanyi_env
    with session_factory() as s:
        # 用户 A 的冲突
        m_a = MemoryItem(
            id="mem-a",
            content="用户A的专有商业机密数据",
            confidence=0.9,
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
        conf_a = ConflictEvent(
            id="conf-a",
            memory_id="mem-a",
            incoming_ref="用户A机密数据更新",
            status="open",
        )
        # 用户 B 的记忆
        m_b = MemoryItem(
            id="mem-b",
            content="用户B的普通工作日志",
            confidence=0.4,
            tenant_id="tenant-B",
            user_id="user-B",
            status="active",
        )
        s.add(m_a)
        s.add(conf_a)
        s.add(m_b)
        s.commit()

        engine = TanyiProbingEngine()
        principal_b = Principal(user_id="user-B", tenant_id="tenant-B", role="user")

        # 传入全量混合列表，引擎应对 Principal B 进行行级过滤
        probes_for_b = engine.generate_probes(
            query="商业机密与工作日志",
            recalled_memories=[m_a, m_b],
            unresolved_conflicts=[conf_a],
            session=s,
            principal=principal_b,
        )

        target_ids = {p.target_id for p in probes_for_b}
        assert "mem-a" not in target_ids
        assert "mem-b" in target_ids
