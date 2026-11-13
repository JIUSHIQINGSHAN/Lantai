"""票 21：沉潜折叠碎片只删 FTS 不删向量——与 promoter 同语义实现直接矛盾。

病灶（`.scratch/v0.68-next-window-2026-11/issues/21-consolidation-fragment-vector-residue.md`，
`consolidation_service.py:474-479`）：折叠循环只 `sync_fts(s, m.id, None)` 删碎片
FTS 行，Chroma 里碎片向量原样留下——碎片幽灵与主记忆语义近同（主记忆即碎片合写），
向量通道里 N 条幽灵与主记忆竞争 fetch_n 名额，`_query_items` 按 status=="active"
静默吞（无日志），极端时主记忆自己的向量被挤出预算——刚沉潜完的主记忆在向量
通道欠召回。

对照范式（自证遗漏非设计）：promoter.py:470-481 consolidation apply 分支对碎片
三件事——status="consolidated" + sync_fts(src, None) + delete_memory_item(src.id)。
修复序沿用本仓「SQL 先 commit、向量后删」统一次序（routes_terminal.py:383-385 /
promoter.py:799 同款）：commit 后逐碎片 delete_memory_item，单条失败 try/except +
logger.warning 留痕（与 :429-433 失败可见口径一致），不回滚已提交的折叠。

替身面声明：向量库用记账替身（外部存储副作用面，纪律允许——在 param_env 的静默
DummyVS 之上再钉记账替身，记录 add/delete 并模拟成员语义供 search 级断言）；复职
臂替 embed（record_ops_service.py:21 顶层绑定先于 param_env 打点，须钉本模块绑定
——外部 LLM 边界，纪律允许）；LLM 提纯段替身沿用既有文件形态（chat_json）；
折叠/FTS/复职全链零 mock。
"""

import logging
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import text

import lantai.retrieval.hybrid as hybrid_module
import lantai.storage.vector_store as vector_store_module
from lantai.models.tables import MemoryItem
from lantai.services.consolidation_service import (
    consolidate_cluster,
    find_consolidation_clusters,
)
from lantai.services.record_ops_service import revive_consolidated
from lantai.storage.fts import sync_fts

_FRAG_IDS = ["mem_frag_01", "mem_frag_02", "mem_frag_03"]

_LLM_PURIFIED = {
    "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
    "importance": 0.9,
    "confidence": 0.95,
}

# 属主四元组：从首碎片继承（ADR-0050 决策 3，与既有索引同步测试同构）
_OWNER = {
    "tenant_id": "t_acme",
    "user_id": "u_brother",
    "agent_id": "",
    "session_id": "s_tea",
}

_EIGHT_KEYS = {
    "key",
    "memory_type",
    "lane",
    "domain",
    "tenant_id",
    "user_id",
    "session_id",
    "agent_id",
}


class _Ledger:
    """向量库记账替身（外部存储副作用面）：记录 add/delete + 成员语义。

    `fail_on` 里的 id 会让 delete 抛错——失败留痕臂的注入点。search 返回
    「在场且未被删」的 id（distance 定值），支撑 search 级断言；不做嵌入数学
    （替身只替外部存储的增删查，不替任何被测逻辑）。
    """

    def __init__(self):
        self.added: list[tuple[list, list]] = []
        self.deleted: list[list] = []
        self.fail_on: set[str] = set()
        self._members: dict[str, dict] = {}

    def add(self, *, ids, embeddings, metadatas):
        self.added.append((list(ids), [dict(m) for m in metadatas]))
        for i, m in zip(ids, metadatas):
            self._members[i] = m

    def delete(self, ids):
        if self.fail_on & set(ids):
            raise RuntimeError(
                f"simulated vector delete failure: {sorted(self.fail_on & set(ids))}"
            )
        self.deleted.append(list(ids))
        for i in ids:
            self._members.pop(i, None)

    def has(self, memory_id: str) -> bool:
        return memory_id in self._members

    def search(self, query_embedding, top_k=10, filters=None):
        return [{"id": i, "distance": 0.5, "metadata": m} for i, m in self._members.items()][:top_k]

    def reset_calls(self):
        """清空调用记账但保留成员语义（复职臂隔离折叠期调用用）。"""
        self.added.clear()
        self.deleted.clear()


@contextmanager
def _ledger_vs():
    """在 param_env 的静默 DummyVS 之上钉记账替身（同两条向量缝）。

    用测试体内 mock.patch（with 退出即还原，先于 fixture teardown）而非
    monkeypatch fixture——param_env 内嵌自己的 MonkeyPatch.context 管同一属性，
    两层打点若靠 fixture 栈还原，teardown 顺序会把 param_env 替身写回（conftest
    模块绑定护栏判「未申报残留」并令会话红）。
    """
    led = _Ledger()
    with (
        patch.object(hybrid_module, "get_vector_store", lambda: led),
        patch.object(vector_store_module, "get_vector_store", lambda: led),
    ):
        yield led


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


def _fold(session_factory, ledger):
    """真折叠一次（前置真值锁：碎片向量/FTS 行在场——模拟 /add 时已入库被索引）。"""
    with session_factory() as s:
        _seed_fragments(s, owner=_OWNER)
        ledger.add(
            ids=_FRAG_IDS,
            embeddings=[[0.1] * 8 for _ in _FRAG_IDS],
            metadatas=[dict(_OWNER) for _ in _FRAG_IDS],
        )
        for fid in _FRAG_IDS:
            item = s.get(MemoryItem, fid)
            sync_fts(s, fid, item.content)
        s.commit()
        assert all(ledger.has(f) for f in _FRAG_IDS), "前置条件：碎片向量在场"
        assert all(_fts_content(s, f) is not None for f in _FRAG_IDS), "前置条件：碎片 FTS 行在场"
        cluster = _cluster_of_three(s)
        with patch(
            "lantai.services.consolidation_service.chat_json",
            return_value=dict(_LLM_PURIFIED),
        ):
            return consolidate_cluster(cluster, session=s)


class TestFragmentVectorCleanup:
    """主臂（现状必红）：折叠后替身 delete 调用列表必须包含全部碎片 id。"""

    def test_fragment_vectors_deleted_and_master_added_with_eight_keys(self, param_env):
        session_factory, _ = param_env
        with _ledger_vs() as ledger:
            master = _fold(session_factory, ledger)

            deleted = [i for call in ledger.deleted for i in call]
            missing = [f for f in _FRAG_IDS if f not in deleted]
            assert not missing, (
                f"碎片向量残条：折叠后 delete 调用缺 {missing}（实际 {ledger.deleted}）——"
                "碎片幽灵与主记忆竞争 fetch_n 名额，极端时主记忆在向量通道欠召回"
            )
            recalled = [h["id"] for h in ledger.search([0.0] * 8, top_k=10)]
            leaked = [f for f in _FRAG_IDS if f in recalled]
            assert not leaked, f"vector_store.search 仍召回已折叠碎片：{leaked}"

            added_meta = {i: m for ids, metas in ledger.added for i, m in zip(ids, metas)}
            assert master.id in added_meta, "主记忆未被向量索引"
            miss_keys = _EIGHT_KEYS - set(added_meta[master.id])
            assert not miss_keys, f"主记忆 metadata 缺键 {sorted(miss_keys)}（8 键契约）"

            # 假绿防线（真库断言，防替身记账与真库脱节的空转锁）
            with session_factory() as s:
                for fid in _FRAG_IDS:
                    assert s.get(MemoryItem, fid).status == "consolidated", fid
                assert s.get(MemoryItem, master.id).status == "active"
                leftovers = {
                    f: _fts_content(s, f) for f in _FRAG_IDS if _fts_content(s, f) is not None
                }
                assert not leftovers, f"折叠后碎片 FTS 行未清理：{leftovers}"


class TestFailureVisibility:
    """失败留痕臂（现状必红）：单条向量删除失败不回滚已提交的折叠，warning 落日志。"""

    def test_single_delete_failure_keeps_fold_and_logs(self, param_env, caplog):
        session_factory, _ = param_env
        with caplog.at_level(logging.WARNING, logger="lantai"):
            with _ledger_vs() as ledger:
                ledger.fail_on = {"mem_frag_02"}
                master = _fold(session_factory, ledger)  # 不抛——折叠主流程不回滚

        with session_factory() as s:
            frag_states = {f: s.get(MemoryItem, f).status for f in _FRAG_IDS}
            assert all(v == "consolidated" for v in frag_states.values()), (
                f"单条向量删除失败把已提交的折叠回滚了：{frag_states}"
            )
            assert s.get(MemoryItem, master.id).status == "active"

        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("mem_frag_02" in m and "向量" in m for m in messages), (
            f"碎片向量删除失败无留痕（记录 {messages}）"
        )


class TestReviveRebuildsVector:
    """复职回归锁（现状即绿，如实申报）：折叠后复职一片，向量重建在场零回归。"""

    def test_revive_reupserts_fragment_vector(self, param_env):
        session_factory, _ = param_env
        with _ledger_vs() as ledger:
            _fold(session_factory, ledger)
            ledger.reset_calls()  # 隔离折叠期调用；成员语义保留（折叠后碎片向量已不在场）

            # record_ops_service.py:21 顶层绑定先于 param_env 打点——替身钉本模块
            # 绑定（测试体内 patch，with 退出即还原，不留跨测绑定残条）。
            # embed 是外部 LLM 边界，纪律允许替身。
            with patch(
                "lantai.services.record_ops_service.embed",
                lambda texts, **kw: [[0.2] * 8 for _ in texts],
            ):
                with session_factory() as s:
                    out = revive_consolidated(
                        "mem_frag_01",
                        reason="票 21 复职回归锁",
                        actor="t21",
                        session=s,
                        principal=None,
                    )
            assert out.get("ok") is True, out

            added = [i for ids, _metas in ledger.added for i in ids]
            assert "mem_frag_01" in added, f"复职未重建碎片向量（ledger.added={ledger.added}）"
            assert ledger.has("mem_frag_01"), "复职后碎片向量不在场"
            with session_factory() as s:
                assert s.get(MemoryItem, "mem_frag_01").status == "active"
                assert _fts_content(s, "mem_frag_01") is not None, "复职后 FTS 未重建"


class TestGreenlineInvariants:
    """假绿防线（现状即绿，如实申报）：真库断言折叠语义本身成立——替身记账
    断言不建立在空转的真库上。"""

    def test_fragments_consolidated_and_fts_cleared(self, param_env):
        session_factory, _ = param_env
        with _ledger_vs() as ledger:
            master = _fold(session_factory, ledger)
        with session_factory() as s:
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "consolidated", fid
            stored = s.get(MemoryItem, master.id)
            assert stored.status == "active"
            assert set(stored.source_ids) == set(_FRAG_IDS)
            leftovers = {f: _fts_content(s, f) for f in _FRAG_IDS if _fts_content(s, f) is not None}
            assert not leftovers, f"折叠后碎片 FTS 行未清理：{leftovers}"
