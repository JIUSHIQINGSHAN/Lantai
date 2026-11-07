"""purge_facts_for_memory 真冒烟（v0.68 测试诚实性审计补强）。

审计结论：完璧级联删除 `purge_facts_for_memory` 此前 mocked-only——仅有的
引用是 X-01 级联回归测试把它 patch 成异常来验证「事实删不掉 → 记忆也不删」
（合法的调用方失败路径），函数自身的主路径从未真实执行。本测试在 param_env
真库上真实构造 FactAtomic + FactEntity（SQLite 未开外键级联，投影必须显式删）
直调之，验证物理删除与返回计数。
"""

import pytest


def test_purge_facts_removes_atoms_and_entity_projections(param_env):
    from sqlmodel import select

    from lantai.models.tables import FactAtomic, FactEntity
    from lantai.services.record_ops_service import purge_facts_for_memory

    session_factory, _ = param_env
    mid = "mem-purge-smoke"
    with session_factory() as s:
        s.add(
            FactAtomic(
                id="fact-purge-a",
                statement="A 使用茶壶",
                predicate="使用",
                object="茶壶",
                source_memory_id=mid,
            )
        )
        s.add(
            FactAtomic(
                id="fact-purge-b",
                statement="B 收藏青瓷",
                predicate="收藏",
                object="青瓷",
                source_memory_id=mid,
            )
        )
        s.add(
            FactAtomic(
                id="fact-keep",
                statement="无关事实",
                predicate="无关",
                object="别处",
                source_memory_id="mem-other",
            )
        )
        s.add(FactEntity(fact_id="fact-purge-a", entity_name="A", role="subject"))
        s.add(FactEntity(fact_id="fact-purge-a", entity_name="茶壶", role="object"))
        s.add(FactEntity(fact_id="fact-purge-b", entity_name="青瓷", role="object"))
        s.add(FactEntity(fact_id="fact-keep", entity_name="别处", role="object"))
        s.commit()

        purged = purge_facts_for_memory(s, mid)
        s.commit()

    assert purged == 2
    with session_factory() as s:
        remaining_facts = {f.id for f in s.exec(select(FactAtomic)).all()}
        assert remaining_facts == {"fact-keep"}, "目标记忆的事实未删净 / 误伤无关事实"
        remaining_entities = {e.fact_id for e in s.exec(select(FactEntity)).all()}
        assert remaining_entities == {"fact-keep"}, "实体投影未级联显式删除"


def test_purge_facts_noop_when_no_facts(param_env):
    from lantai.services.record_ops_service import purge_facts_for_memory

    session_factory, _ = param_env
    with session_factory() as s:
        assert purge_facts_for_memory(s, "mem-never-had-facts") == 0
