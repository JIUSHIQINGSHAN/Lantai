"""探颐·TanyiEngine 存储驱动主动探针引擎冒烟测试 (v0.38 Ticket 01)

基于真实 in-memory SQLite，不 mock 内部计算逻辑。
验证接缝（Seams）：
1. TanyiEngine.probe_ambiguities(tenant_id, ...) 主动扫描租户内低置信度事实与未决冲突，
   输出 TanyiProbeItem 探针候选列表；
2. query 关联过滤与 limit 截断；
3. TanyiEngine.generate_clarification_prompts(probes) 生成面向用户的自然对话追问；
4. 跨租户强隔离：探针绝不越租户泄漏（租户隔离 100% 遵守）。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition import TanyiEngine
from lantai.models.tables import ConflictEvent, MemoryItem


@pytest.fixture()
def tanyi_engine_env():
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


def _seed_tenant_a(session: Session) -> None:
    """租户 A：一条未决冲突事实 + 一条低置信度事实 + 一条高置信度正常事实。"""
    session.add(
        MemoryItem(
            id="mem-a-conflict",
            content="团队核心常驻办公地点在上海张江",
            confidence=0.9,
            lane="fact",
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
    )
    session.add(
        ConflictEvent(
            id="conf-a-1",
            memory_id="mem-a-conflict",
            incoming_ref="团队近期已搬迁至北京中关村办公",
            rule_name="location_conflict",
            status="open",
        )
    )
    session.add(
        MemoryItem(
            id="mem-a-fuzzy",
            content="用户偏好喝冷萃不加糖咖啡",
            confidence=0.45,
            lane="preference",
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
    )
    session.add(
        MemoryItem(
            id="mem-a-solid",
            content="用户的正式抬头是高级工程师",
            confidence=0.95,
            lane="fact",
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
    )
    session.commit()


def test_tanyi_engine_probes_low_confidence_and_open_conflicts(tanyi_engine_env):
    """Seam 1: 主动扫描租户内未决冲突与低置信度事实，高置信度事实不打扰。"""
    session_factory = tanyi_engine_env
    with session_factory() as s:
        _seed_tenant_a(s)

        engine = TanyiEngine(storage=s)
        probes = engine.probe_ambiguities(tenant_id="tenant-A", limit=10)

        memory_ids = {p.memory_id for p in probes}
        # 未决冲突挂载记忆必须被探出
        assert "mem-a-conflict" in memory_ids
        # 低置信度（0.45 < 0.6 阈值）事实必须被探出
        assert "mem-a-fuzzy" in memory_ids
        # 高置信度（0.95）事实不属于模糊事实，不应产生探针
        assert "mem-a-solid" not in memory_ids

        conflict_probe = next(p for p in probes if p.memory_id == "mem-a-conflict")
        assert conflict_probe.conflict_id == "conf-a-1"
        fuzzy_probe = next(p for p in probes if p.memory_id == "mem-a-fuzzy")
        assert fuzzy_probe.conflict_id is None
        assert fuzzy_probe.confidence == pytest.approx(0.45)


def test_tanyi_engine_query_filter_and_limit(tanyi_engine_env):
    """Seam 2: query 关联过滤只保留相关探针；limit 截断探针数量。"""
    session_factory = tanyi_engine_env
    with session_factory() as s:
        _seed_tenant_a(s)

        engine = TanyiEngine(storage=s)
        probes = engine.probe_ambiguities(tenant_id="tenant-A", query="办公地点", limit=10)
        assert probes, "query 命中冲突事实时应至少产出一条探针"
        assert all("办公" in p.content or "办公" in (p.incoming_ref or "") for p in probes)

        limited = engine.probe_ambiguities(tenant_id="tenant-A", limit=1)
        assert len(limited) == 1


def test_tanyi_engine_generate_clarification_prompts(tanyi_engine_env):
    """Seam 3: 探针转换为面向用户的自然对话追问插桩建议。"""
    session_factory = tanyi_engine_env
    with session_factory() as s:
        _seed_tenant_a(s)

        engine = TanyiEngine(storage=s)
        probes = engine.probe_ambiguities(tenant_id="tenant-A", limit=10)
        prompts = engine.generate_clarification_prompts(probes)

        assert isinstance(prompts, list)
        assert len(prompts) == len(probes)
        for prompt in prompts:
            assert isinstance(prompt, str) and prompt.strip()
        # 冲突探针的追问应同时呈现旧主张与新主张，便于用户裁决
        conflict_probe = next(p for p in probes if p.conflict_id == "conf-a-1")
        conflict_prompt = prompts[probes.index(conflict_probe)]
        assert "上海张江" in conflict_prompt
        assert "北京中关村" in conflict_prompt


def test_tanyi_engine_cross_tenant_isolation(tanyi_engine_env):
    """Seam 4: 跨租户 100% 隔离——探租户 A 时绝不泄漏租户 B 的任何事实。"""
    session_factory = tanyi_engine_env
    with session_factory() as s:
        _seed_tenant_a(s)
        # 租户 B 的敏感低置信度事实 + 未决冲突
        s.add(
            MemoryItem(
                id="mem-b-secret",
                content="租户B的薪酬调整预案",
                confidence=0.4,
                tenant_id="tenant-B",
                user_id="user-B",
                status="active",
            )
        )
        s.add(
            ConflictEvent(
                id="conf-b-1",
                memory_id="mem-b-secret",
                incoming_ref="租户B薪酬预案改为普调 8%",
                rule_name="comp_conflict",
                status="open",
            )
        )
        s.commit()

        engine = TanyiEngine(storage=s)
        probes_a = engine.probe_ambiguities(tenant_id="tenant-A", limit=50)
        memory_ids_a = {p.memory_id for p in probes_a}
        assert "mem-b-secret" not in memory_ids_a
        assert all(p.tenant_id == "tenant-A" for p in probes_a)

        probes_b = engine.probe_ambiguities(tenant_id="tenant-B", limit=50)
        memory_ids_b = {p.memory_id for p in probes_b}
        assert memory_ids_b == {"mem-b-secret"}
        assert all(p.tenant_id == "tenant-B" for p in probes_b)
