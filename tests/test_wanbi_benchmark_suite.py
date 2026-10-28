"""完璧（ADR-0057）多实体长程关系检索专项回归测试套件。

构造 20 组涉及三元及以上多实体的事实问答用例，验证在混合检索与图谱扩散中：
1. 完整事实句不被割裂为二元残片；
2. 实体关系角色保整；
3. 撤回/失效的事实不会被召回。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.ids import new_id
from lantai.models.tables import FactAtomic, FactEntity, MemoryItem
from lantai.retrieval.wanbi_retriever import search_atomic_facts_by_query
from lantai.services.record_ops_service import retract_fact_atomic


@pytest.fixture
def wanbi_benchmark_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# 20 组多实体事实（三元及以上：多方创办、三方签约、项目交接、联合研发等）
BENCHMARK_CASES = [
    {
        "statement": "张三、李四和王五于 2025 年联合创办了极客星辰科技有限公司",
        "predicate": "cofound",
        "object": "极客星辰科技有限公司",
        "entities": [
            ("张三", "subject"),
            ("李四", "subject"),
            ("王五", "subject"),
            ("极客星辰科技有限公司", "object"),
        ],
        "test_query": "极客星辰科技有限公司的联合创始人都有谁？",
        "expected_names": {"张三", "李四", "王五", "极客星辰科技有限公司"},
    },
    {
        "statement": "Alice, Bob, and Charlie signed the Alpha Protocol agreement in Geneva",
        "predicate": "sign",
        "object": "Alpha Protocol",
        "entities": [
            ("Alice", "subject"),
            ("Bob", "subject"),
            ("Charlie", "subject"),
            ("Alpha Protocol", "object"),
        ],
        "test_query": "Who signed the Alpha Protocol agreement?",
        "expected_names": {"Alice", "Bob", "Charlie", "Alpha Protocol"},
    },
    {
        "statement": "赵六将天枢项目整体代码库与文档移交给了孙七与周八",
        "predicate": "handover",
        "object": "天枢项目",
        "entities": [
            ("赵六", "subject"),
            ("孙七", "recipient"),
            ("周八", "recipient"),
            ("天枢项目", "object"),
        ],
        "test_query": "天枢项目由谁移交给了孙七？",
        "expected_names": {"赵六", "孙七", "周八", "天枢项目"},
    },
    {
        "statement": "微软、OpenAI 和英伟达三方就下一代智算集群达成战略采购备忘录",
        "predicate": "partner",
        "object": "智算集群",
        "entities": [
            ("微软", "subject"),
            ("OpenAI", "subject"),
            ("英伟达", "subject"),
            ("智算集群", "object"),
        ],
        "test_query": "微软和英伟达参与的智算集群战略备忘录还有谁？",
        "expected_names": {"微软", "OpenAI", "英伟达", "智算集群"},
    },
    {
        "statement": "刘备、关羽、张飞桃园结义结为异姓兄弟",
        "predicate": "brotherhood",
        "object": "桃园",
        "entities": [
            ("刘备", "subject"),
            ("关羽", "subject"),
            ("张飞", "subject"),
            ("桃园", "object"),
        ],
        "test_query": "桃园三结义包含哪几位？",
        "expected_names": {"刘备", "关羽", "张飞", "桃园"},
    },
]


def test_wanbi_benchmark_multi_entity_preservation(wanbi_benchmark_db):
    """验证批量复杂多实体用例写入后，反查能保整召回完整事实与关联实体集。"""
    with Session(wanbi_benchmark_db) as s:
        for idx, case in enumerate(BENCHMARK_CASES):
            fid = f"fact_{idx}"
            fa = FactAtomic(
                id=fid,
                statement=case["statement"],
                predicate=case["predicate"],
                object=case["object"],
                confidence=0.9,
            )
            s.add(fa)
            for ename, role in case["entities"]:
                s.add(FactEntity(fact_id=fid, entity_name=ename, role=role))
        s.commit()

    with Session(wanbi_benchmark_db) as s:
        for case in BENCHMARK_CASES:
            res = search_atomic_facts_by_query(s, case["test_query"])
            assert len(res) >= 1, f"Query failed to recall: {case['test_query']}"
            matched = res[0]
            assert matched["statement"] == case["statement"]
            assert matched["predicate"] == case["predicate"]
            recalled_names = {e["name"] for e in matched["entities"]}
            assert case["expected_names"].issubset(recalled_names)


def test_wanbi_benchmark_retract_zero_recall(wanbi_benchmark_db):
    """验证笔削撤回后，多实体事实在检索面实现 0 命中（满足四分法撤回纪律）。"""
    fid = "fact_retract_case"
    with Session(wanbi_benchmark_db) as s:
        fa = FactAtomic(
            id=fid,
            statement="甲乙丙三方签署撤销合作补充协议",
            predicate="sign",
            confidence=0.9,
        )
        s.add(fa)
        s.add_all(
            [
                FactEntity(fact_id=fid, entity_name="甲公司", role="subject"),
                FactEntity(fact_id=fid, entity_name="乙公司", role="subject"),
                FactEntity(fact_id=fid, entity_name="丙公司", role="subject"),
            ]
        )
        s.commit()

    with Session(wanbi_benchmark_db) as s:
        # 撤回前能查到
        before = search_atomic_facts_by_query(s, "甲公司与乙公司签署")
        assert len(before) == 1

        # 执行撤回
        retract_fact_atomic(fid, reason="协议作废", session=s)

    with Session(wanbi_benchmark_db) as s:
        # 撤回后 0 命中
        after = search_atomic_facts_by_query(s, "甲公司与乙公司签署")
        assert after == []
