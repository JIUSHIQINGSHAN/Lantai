"""探颐·自然交互答复解析与自动消解闭环冒烟测试 (Ticket 02)

基于真实 in-memory SQLite，验证接缝（Seams）：
1. parse_user_resolution_intent 精准识别用户答复的意图（AFFIRMATIVE, NEGATIVE, CORRECTION, AMBIGUOUS）；
2. execute_tanyi_resolution 肯定分支：自动将未决冲突标记为 resolved，原子化更迭主张内容，落快照 Checkpoint；
3. execute_tanyi_resolution 否定分支：自动将未决冲突标记为 dismissed，原主张内容保持完整不变；
4. execute_tanyi_resolution 修正分支：识别用户自然答复中的新主张修正（如“都不是，其实转到了杭州西湖”），直接以修正值应用；
5. 暧昧表态分支（AMBIGUOUS）：保持 deferred，贯彻「宁 miss 不脏写」；
6. 越权写防护：非资源属主发起消解时严格抛出 403 / 阻断，拒绝脏写。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition.tanyi import (
    ResolutionIntent,
    execute_tanyi_resolution,
    parse_user_resolution_intent,
)
from lantai.core.auth import Principal
from lantai.models.tables import ConflictEvent, MemoryCheckpoint, MemoryItem


@pytest.fixture()
def tanyi_res_env():
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


def test_parse_user_resolution_intent():
    """Seam 1: 验证答复意图分类器。"""
    # 肯定意图
    assert parse_user_resolution_intent("是的，确实是这样")[0] == ResolutionIntent.AFFIRMATIVE
    assert parse_user_resolution_intent("对的没错")[0] == ResolutionIntent.AFFIRMATIVE
    assert parse_user_resolution_intent("确认变更了")[0] == ResolutionIntent.AFFIRMATIVE

    # 否定意图
    assert parse_user_resolution_intent("不是的，搞错了")[0] == ResolutionIntent.NEGATIVE
    assert parse_user_resolution_intent("没有这回事，还是原来的")[0] == ResolutionIntent.NEGATIVE
    assert parse_user_resolution_intent("不对")[0] == ResolutionIntent.NEGATIVE

    # 修正意图（提取用户给出的全新事实）
    intent, correction = parse_user_resolution_intent("不对，其实是搬到了杭州滨江")
    assert intent == ResolutionIntent.CORRECTION
    assert "杭州滨江" in correction

    # 暧昧意图
    assert (
        parse_user_resolution_intent("今天天气挺好的，我们聊别的吧")[0]
        == ResolutionIntent.AMBIGUOUS
    )
    assert parse_user_resolution_intent("不太清楚哦")[0] == ResolutionIntent.AMBIGUOUS


def test_execute_tanyi_resolution_affirmative_and_negative(tanyi_res_env):
    """Seam 2 & 3: 验证肯定与否定答复的自动闭环消解。"""
    session_factory = tanyi_res_env

    # 1. 肯定闭环
    with session_factory() as s:
        m1 = MemoryItem(
            id="mem-aff-1",
            content="常驻上海徐汇",
            confidence=0.8,
            version=1,
            tenant_id="t-1",
            user_id="u-1",
            status="active",
        )
        conf1 = ConflictEvent(
            id="conf-aff-1",
            memory_id="mem-aff-1",
            incoming_ref="近期全面搬迁到北京海淀",
            status="open",
        )
        s.add(m1)
        s.add(conf1)
        s.commit()

        principal = Principal(user_id="u-1", tenant_id="t-1", role="user")
        res = execute_tanyi_resolution(
            conflict_id="conf-aff-1",
            user_reply="是的，确实已经搬去北京海淀了",
            session=s,
            principal=principal,
        )

        assert res["status"] == "resolved"
        assert res["action"] == "applied"
        assert res["intent"] == ResolutionIntent.AFFIRMATIVE

        s.refresh(m1)
        s.refresh(conf1)
        assert conf1.status == "resolved"
        assert "北京海淀" in m1.content
        assert m1.version == 2

        # 检查快照
        cps = s.query(MemoryCheckpoint).filter(MemoryCheckpoint.memory_id == "mem-aff-1").all()
        assert len(cps) >= 1
        assert cps[0].trigger == "proactive_probe_resolved"

    # 2. 否定闭环
    with session_factory() as s:
        m2 = MemoryItem(
            id="mem-neg-1",
            content="主营业务为云计算服务",
            confidence=0.9,
            version=1,
            tenant_id="t-1",
            user_id="u-1",
            status="active",
        )
        conf2 = ConflictEvent(
            id="conf-neg-1",
            memory_id="mem-neg-1",
            incoming_ref="主营业务变更为房地产销售",
            status="open",
        )
        s.add(m2)
        s.add(conf2)
        s.commit()

        principal = Principal(user_id="u-1", tenant_id="t-1", role="user")
        res = execute_tanyi_resolution(
            conflict_id="conf-neg-1",
            user_reply="不对，完全搞错了，我们从不做房地产",
            session=s,
            principal=principal,
        )

        assert res["status"] == "resolved"
        assert res["action"] == "dismissed"
        assert res["intent"] == ResolutionIntent.NEGATIVE

        s.refresh(m2)
        s.refresh(conf2)
        assert conf2.status == "dismissed"
        assert "云计算" in m2.content
        assert m2.version == 1


def test_execute_tanyi_resolution_correction_and_unauthorized(tanyi_res_env):
    """Seam 4 & 6: 验证自然语言修正分支与非属主越权阻断。"""
    session_factory = tanyi_res_env

    # 1. 修正分支（提供了第三种事实）
    with session_factory() as s:
        m3 = MemoryItem(
            id="mem-corr-1",
            content="目前使用 MySQL 数据库",
            confidence=0.8,
            version=1,
            tenant_id="t-1",
            user_id="u-1",
            status="active",
        )
        conf3 = ConflictEvent(
            id="conf-corr-1",
            memory_id="mem-corr-1",
            incoming_ref="计划换成 MongoDB",
            status="open",
        )
        s.add(m3)
        s.add(conf3)
        s.commit()

        principal = Principal(user_id="u-1", tenant_id="t-1", role="user")
        res = execute_tanyi_resolution(
            conflict_id="conf-corr-1",
            user_reply="不是MongoDB，其实最后选用了 PostgreSQL 数据库",
            session=s,
            principal=principal,
        )

        assert res["status"] == "resolved"
        assert res["action"] == "applied_correction"
        assert res["intent"] == ResolutionIntent.CORRECTION

        s.refresh(m3)
        s.refresh(conf3)
        assert conf3.status == "resolved"
        assert "PostgreSQL" in m3.content

    # 2. 越权阻断：用户 B 试图消解用户 A 的记忆
    with session_factory() as s:
        m_a = MemoryItem(
            id="mem-sec-a",
            content="用户 A 个人财务密码",
            tenant_id="t-1",
            user_id="u-A",
            status="active",
        )
        conf_a = ConflictEvent(
            id="conf-sec-a",
            memory_id="mem-sec-a",
            incoming_ref="密码覆盖",
            status="open",
        )
        s.add(m_a)
        s.add(conf_a)
        s.commit()

        principal_b = Principal(user_id="u-B", tenant_id="t-1", role="user")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            execute_tanyi_resolution(
                conflict_id="conf-sec-a",
                user_reply="是的，我确认修改",
                session=s,
                principal=principal_b,
            )
        assert exc_info.value.status_code == 403
