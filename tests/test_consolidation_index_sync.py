"""沉潜 off 模式索引同步：票 .scratch/consolidation-index-sync/01

off 模式（**生产默认**）把碎片折叠成主记忆，但主记忆的索引是残的：
零 `sync_fts`（词汇召回通道永久不可见）、属主四元组全 NULL、
metadata 只有 6 键、碎片索引行从不清理、且 `s.commit()` 在向量索引之前
（索引失败只留一行 warning，无重试无重建，碎片已不可逆折叠）。

本文件的测试全部**不 mock 内部计算逻辑**：真实内存库、真实 FTS、真实
`consolidate_cluster` 全流程；只替外部副作用（LLM 提纯段、向量存储），
以及为验证「向量失败可恢复」而强制 `embed` 抛异常。

对齐的唯一正确范式见 `memory_service.py:429-443`：
    s.add(mem) → s.flush() → index_memory_item(...) → sync_fts(...) → s.commit()
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from lantai.models.tables import MemoryItem
from lantai.services.consolidation_service import (
    consolidate_cluster,
    find_consolidation_clusters,
)

_FRAG_IDS = ["mem_frag_01", "mem_frag_02", "mem_frag_03"]

_LLM_PURIFIED = {
    "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
    "importance": 0.9,
    "confidence": 0.95,
}

# 属主四元组：off 模式应当从首碎片继承（ADR-0050 决策 3）
_OWNER = {
    "tenant_id": "t_acme",
    "user_id": "u_brother",
    "agent_id": "",
    "session_id": "s_tea",
}


def _seed_fragments(s, owner=None):
    """3 条同主题偏好碎片（与 tests/test_consolidation.py 同构，保证聚类稳定命中）。"""
    owner = owner or {}
    s.add_all(
        [
            MemoryItem(
                id="mem_frag_01",
                content="大哥今天早上泡了明前大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
                **owner,
            ),
            MemoryItem(
                id="mem_frag_02",
                content="大哥喜欢喝大佛龙井茶，水温要求85度",
                lane="preference",
                domain="user",
                decay_score=0.88,
                status="active",
                **owner,
            ),
            MemoryItem(
                id="mem_frag_03",
                content="大哥日常饮品偏好为浙江新昌大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.85,
                status="active",
                **owner,
            ),
        ]
    )
    s.commit()


def _cluster_of_three(s):
    clusters = find_consolidation_clusters(s, min_cluster_size=3)
    assert len(clusters) >= 1
    return clusters[0]


def _fts_content(s, memory_id):
    row = (
        s.connection()
        .execute(text("SELECT content FROM memory_fts WHERE memory_id = :mid"), {"mid": memory_id})
        .fetchone()
    )
    return row[0] if row else None


def _run_off(session_factory):
    """跑一次真实 off 模式折叠，返回主记忆。"""
    with session_factory() as s:
        cluster = _cluster_of_three(s)
        with patch(
            "lantai.services.consolidation_service.chat_json",
            return_value=dict(_LLM_PURIFIED),
        ):
            res = consolidate_cluster(cluster, session=s)
        return res


class TestMasterIsIndexed:
    """主记忆必须在词汇通道里存在、必须带属主、metadata 必须齐 8 键。"""

    def test_master_has_fts_row(self, param_env):
        """Red 1：off 跑完，主记忆在 FTS 里有行（现状：无行，词汇召回永久不可见）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
        master = _run_off(session_factory)

        with session_factory() as s:
            content = _fts_content(s, master.id)
            assert content is not None, "主记忆无 FTS 行——词汇召回通道永久不可见"
            assert "大佛龙井" in content

    def test_master_fts_row_survives_commit(self, param_env):
        """FTS 行须在**提交后仍可见**（同事务写入的强一致语义，ADR-0008）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
        master = _run_off(session_factory)

        # 全新会话读——排除「同会话未提交脏读」
        with Session(session_factory().get_bind()) as fresh:
            assert _fts_content(fresh, master.id) is not None

    def test_master_inherits_owner_quadruple(self, param_env):
        """Red 2：主记忆继承首碎片属主（现状：四元组全 NULL）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s, owner=_OWNER)
        master = _run_off(session_factory)

        assert master.tenant_id == "t_acme", f"tenant_id={master.tenant_id!r}"
        assert master.user_id == "u_brother", f"user_id={master.user_id!r}"
        assert master.session_id == "s_tea", f"session_id={master.session_id!r}"

    def test_master_visible_under_owner_filter(self, param_env):
        """属主继承的真正意义：主记忆要能在属主过滤检索中出现。

        这是端到端判据——不只看字段传对了，而是真的能被 owner 过滤查到。
        """
        from lantai.core.auth import Principal
        from lantai.retrieval.hybrid import hybrid_search

        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s, owner=_OWNER)
        master = _run_off(session_factory)

        principal = Principal(
            tenant_id="t_acme", user_id="u_brother", agent_id="", session_id="s_tea"
        )
        with session_factory() as s:
            hits = hybrid_search("大佛龙井", session=s, principal=principal, use_rerank=False)
        # hybrid_search 返回 [{"memory": {...}, "score": ...}, ...]
        # （同 tests/test_bixiao_deterministic.py:119 的既有取法）
        ids = [r["memory"]["id"] for r in hits]
        assert master.id in ids, f"主记忆在属主过滤检索中不可见（命中 {ids}）"

    def test_master_vector_metadata_has_eight_keys(self, param_env):
        """Red 3：向量 metadata 8 键契约（record_ops_service.py:90-96 明载）。"""
        session_factory, _ = param_env
        recorded = {}

        import lantai.services.consolidation_service as cs

        real_index = cs.index_memory_item

        def spy(memory_id, embedding, metadata, *a, **kw):
            recorded[memory_id] = dict(metadata)
            return real_index(memory_id, embedding, metadata, *a, **kw)

        with session_factory() as s:
            _seed_fragments(s, owner=_OWNER)
            cluster = _cluster_of_three(s)
            with (
                patch(
                    "lantai.services.consolidation_service.chat_json",
                    return_value=dict(_LLM_PURIFIED),
                ),
                # 须 patch **consolidation_service 自己的绑定**——它是
                # `from ... import index_memory_item`，改源模块不影响已存的引用
                patch.object(cs, "index_memory_item", spy),
            ):
                master = consolidate_cluster(cluster, session=s)

        assert master.id in recorded, "主记忆未被向量索引"
        md = recorded[master.id]
        expected = {
            "key",
            "memory_type",
            "lane",
            "domain",
            "tenant_id",
            "user_id",
            "session_id",
            "agent_id",
        }
        missing = expected - set(md)
        assert not missing, f"metadata 缺键 {sorted(missing)}（现为 {sorted(md)}）"


class TestFragmentIndexCleaned:
    """碎片折叠后，其 FTS 行必须删掉——否则词汇通道召回已折叠内容。"""

    def test_fragment_fts_rows_removed(self, param_env):
        """Red 4：折叠后碎片 FTS 行已删（现状：行还在）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
            # 折叠前碎片本应有 FTS 行（模拟真实 /add 写入路径留下的索引）
            for fid in _FRAG_IDS:
                item = s.get(MemoryItem, fid)
                from lantai.storage.fts import sync_fts

                sync_fts(s, fid, item.content)
            s.commit()
            assert all(_fts_content(s, f) is not None for f in _FRAG_IDS), "前置条件：碎片有 FTS 行"

        _run_off(session_factory)

        with session_factory() as s:
            leftovers = {f: _fts_content(s, f) for f in _FRAG_IDS if _fts_content(s, f) is not None}
            assert not leftovers, f"折叠后碎片 FTS 行未清理：{leftovers}"

    def test_lexical_search_returns_master_not_fragments(self, param_env):
        """端到端：词汇召回应命中主记忆、不再命中已折叠碎片。"""
        from lantai.storage.fts import search_fts_bm25

        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
            for fid in _FRAG_IDS:
                item = s.get(MemoryItem, fid)
                from lantai.storage.fts import sync_fts

                sync_fts(s, fid, item.content)
            s.commit()
        master = _run_off(session_factory)

        with session_factory() as s:
            hits = [
                mid
                for mid, _ in search_fts_bm25(
                    s.connection().connection.driver_connection, "大佛龙井茶", top_k=10
                )
            ]
        assert master.id in hits, f"主记忆未被词汇召回到（命中 {hits}）"
        leaked = [f for f in _FRAG_IDS if f in hits]
        assert not leaked, f"已折叠碎片仍被词汇召回：{leaked}"


class TestVectorFailureIsRecoverable:
    """Red 5（关键）：向量索引失败不得留下「碎片没了、主记忆搜不到」的孤儿态。"""

    def test_embed_failure_rolls_back_instead_of_orphaning(self, param_env, monkeypatch):
        """强制 embed 抛异常 → 整笔回滚：主记忆不在库、碎片仍 active（可重试）。

        现状（修复前）：主记忆已落库、碎片已折叠、向量没索引、只一行 warning——
        且无重试路径、无重建路径，是**不可恢复的孤儿态**。
        """
        session_factory, _ = param_env

        def boom(*a, **kw):
            raise RuntimeError("embed service unavailable (503)")

        monkeypatch.setattr("lantai.llm.client.embed", boom)

        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                # 新行为：如实上抛，让调用方（worker/CLI）知道本次没做成。
                # 相比旧行为（吞掉 warning 后返回主记忆）这是**更诚实**的失败。
                with pytest.raises(RuntimeError, match="embed service unavailable"):
                    consolidate_cluster(cluster, session=s)

        # 关键断言：回滚后不得留下孤儿态
        with session_factory() as s:
            masters = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()
            frag_states = {
                f: (s.get(MemoryItem, f).status if s.get(MemoryItem, f) else None)
                for f in _FRAG_IDS
            }

        orphaned = bool(masters) and all(v == "consolidated" for v in frag_states.values())
        assert not orphaned, (
            f"孤儿态：主记忆已落库（{[m.id for m in masters]}）+ 碎片已折叠（{frag_states}）"
            "而索引未建成，且无重试/重建路径"
        )
        # 碎片应保持 active（可重新聚类重试）
        assert all(v == "active" for v in frag_states.values()), (
            f"回滚后碎片状态异常（应为 active 以便重试）：{frag_states}"
        )

    def test_embed_failure_is_reported_not_silent(self, param_env, monkeypatch, caplog):
        """失败必须留痕且措辞诚实——不得声称「已落库」而实际没有。"""
        import logging

        session_factory, _ = param_env

        def boom(*a, **kw):
            raise RuntimeError("embed service unavailable (503)")

        monkeypatch.setattr("lantai.llm.client.embed", boom)

        with caplog.at_level(logging.WARNING, logger="lantai"):
            with session_factory() as s:
                _seed_fragments(s)
                cluster = _cluster_of_three(s)
                with (
                    patch(
                        "lantai.services.consolidation_service.chat_json",
                        return_value=dict(_LLM_PURIFIED),
                    ),
                    pytest.raises(RuntimeError, match="embed service unavailable"),
                ):
                    consolidate_cluster(cluster, session=s)

        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        messages = [r.getMessage() for r in records]
        assert any("索引" in m or "向量" in m for m in messages), (
            f"向量索引失败无留痕（记录 {messages}）"
        )
        # 措辞诚实：不得再说「已落库」——事实上会回滚
        assert not any("已落库" in m for m in messages), (
            f"失败留痕仍声称「已落库」，与实际（回滚）不符：{messages}"
        )


class TestNoRegressionOnExistingBehavior:
    """护栏：本票修的是索引同步，不得改动折叠/聚类/零新行等既有语义。"""

    def test_zero_new_rows_preserved(self, param_env):
        """off 零新行铁律（ADR-0050 决策 2）不得被破坏。"""
        from lantai.models.tables import ConsolidationRun, MemoryProposal

        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_LLM_PURIFIED),
            ):
                consolidate_cluster(cluster, session=s)

            assert s.exec(select(MemoryProposal)).all() == []
            assert s.exec(select(ConsolidationRun)).all() == []

    def test_folding_still_happens_on_success(self, param_env):
        """成功路径上碎片仍被折叠、主记忆仍 active、source_ids 仍正确。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_fragments(s)
        master = _run_off(session_factory)

        with session_factory() as s:
            assert master.status == "active"
            assert set(master.source_ids) == set(_FRAG_IDS)
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "consolidated"
