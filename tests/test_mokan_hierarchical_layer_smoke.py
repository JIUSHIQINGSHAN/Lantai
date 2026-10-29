"""磨勘（Mokan · v0.29）MCMA 建议项 4：抽象层级化与选层检索不 mock 冒烟测试。

测试Seams：
1. classify_memory_layer：判断记忆/事实归属的认识论层级（H0原始观察、H1情境事实、H2抽象规则）；
2. detect_query_intent_layer：判定 Query 的意图抽象度偏向（h1具体事实 / h2方法规范 / all通用）；
3. hybrid_search(layer="auto")：端到端真实 SQLite + FTS 检索，验证抽象意图优先召回 H2、具象意图优先召回 H1。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.tables import CognitiveRole, FactAtomic, MemoryItem
from lantai.retrieval import hybrid
from lantai.retrieval.hierarchical import classify_memory_layer, detect_query_intent_layer
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def mokan_test_db(monkeypatch):
    """真实内存 SQLite + FTS，不 mock 内部计算逻辑。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid.db, "get_session", session_factory)
    yield session_factory, engine


def test_classify_memory_layer_pure():
    """Seam 1 单元测试：验证不同认知角色与分轨的记忆能准确映射至 H0/H1/H2。"""
    # H0：原始观察、闲聊草稿
    m_h0 = MemoryItem(
        id=new_id("mem"),
        content="昨天下午测试了一下本地脚本",
        role=CognitiveRole.OBSERVATION,
        lane="general",
    )
    assert classify_memory_layer(m_h0) == "h0"

    # H1：具体事实、经验记录、原子事实
    m_h1 = MemoryItem(
        id=new_id("mem"),
        content="张三在2024年担任星光科技CTO职位",
        role=CognitiveRole.EXPERIENCE,
        lane="fact",
    )
    assert classify_memory_layer(m_h1) == "h1"

    fact_atomic = FactAtomic(
        id=new_id("fact"),
        statement="张三在2024年担任星光科技CTO",
        predicate="position",
        object="CTO",
    )
    assert classify_memory_layer(fact_atomic) == "h1"

    # H2：抽象规则、系统准则、信念
    m_h2_rule = MemoryItem(
        id=new_id("mem"),
        content="Python代码必须遵循PEP8规范，新概念必须登记CONTEXT.md",
        role=CognitiveRole.RULE,
        lane="rule",
    )
    assert classify_memory_layer(m_h2_rule) == "h2"

    m_h2_belief = MemoryItem(
        id=new_id("mem"),
        content="系统架构宁可 miss 绝不脏写",
        role=CognitiveRole.BELIEF,
        lane="preference",
    )
    assert classify_memory_layer(m_h2_belief) == "h2"


def test_detect_query_intent_layer_pure():
    """Seam 2 单元测试：启发式判断 Query 期望激活的抽象层级。"""
    # 抽象方法论 / 规则准则类 -> h2
    assert detect_query_intent_layer("Python 项目命名的核心规范原则是什么？") == "h2"
    assert detect_query_intent_layer("开发流程有什么最佳实践与指南要求？") == "h2"

    # 具象事实 / 时间实体类 -> h1
    assert detect_query_intent_layer("张三在2024年具体担任什么职务？") == "h1"
    assert detect_query_intent_layer("上周五发生的会议记录内容") == "h1"

    # 通用无明确层级偏向 -> all
    assert detect_query_intent_layer("你好，请介绍一下你自己") == "all"


def test_mokan_hierarchical_search_smoke(mokan_test_db):
    """Seam 3 冒烟测试：端到端真实 SQLite + FTS 混合检索中的选层加权与层级透出。"""
    session_factory, engine = mokan_test_db

    mem_h0_id = new_id("mem")
    mem_h1_id = new_id("mem")
    mem_h2_id = new_id("mem")

    with session_factory() as s:
        # H0: 原始观察片段
        m0 = MemoryItem(
            id=mem_h0_id,
            content="我昨天在电脑上写了几个 Python 脚本来测试各种函数的运行情况",
            role=CognitiveRole.OBSERVATION,
            lane="general",
            status="active",
        )
        # H1: 具体业务经验与事实
        m1 = MemoryItem(
            id=mem_h1_id,
            content="星光科技团队的李四工程师完成了 Python 核心数据管道的上线",
            role=CognitiveRole.EXPERIENCE,
            lane="fact",
            status="active",
        )
        # H2: 高阶开发规范与原则
        m2 = MemoryItem(
            id=mem_h2_id,
            content="Python 模块开发的铁律与规范原则：严禁纯 mock 单测，核心函数必须具备不 mock 冒烟",
            role=CognitiveRole.RULE,
            lane="rule",
            status="active",
        )
        s.add_all([m0, m1, m2])
        s.commit()

        # 同步 FTS 索引
        sync_fts(s, m0.id, m0.content)
        sync_fts(s, m1.id, m1.content)
        sync_fts(s, m2.id, m2.content)
        s.commit()

    # 1. 验证抽象规则查询：自动加权 H2，且输出包含 layer 标注
    res_rule = hybrid_search("Python 开发的核心规范原则是什么？", top_k=3, layer="auto")
    assert len(res_rule) >= 1
    # top-1 应精准为 H2 规则记忆
    top_rule = res_rule[0]
    assert top_rule["memory"]["id"] == mem_h2_id
    assert top_rule["layer"] == "h2"
    assert "layer" in top_rule

    # 2. 验证具象事实查询：优先命中 H1
    res_fact = hybrid_search("李四工程师具体完成了什么工作上线？", top_k=3, layer="auto")
    assert len(res_fact) >= 1
    top_fact = res_fact[0]
    assert top_fact["memory"]["id"] == mem_h1_id
    assert top_fact["layer"] == "h1"

    # 3. 验证显式指定 layer="h2"：即使通用查询也精准锁定并过滤/前置 H2
    res_h2_only = hybrid_search("Python", top_k=3, layer="h2")
    assert len(res_h2_only) >= 1
    assert res_h2_only[0]["memory"]["id"] == mem_h2_id
    assert res_h2_only[0]["layer"] == "h2"
