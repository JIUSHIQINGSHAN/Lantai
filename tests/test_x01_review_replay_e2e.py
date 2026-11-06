"""评审 X-01 头条场景端到端重放锁 + delete_memory 三处一致性锁（票 05 续36）。

病灶出处：`docs/research/commit-review-v0.38-v0.60-2026-10.md` 第 1 节结论 3 的实测——
「删掉『我的银行卡密码是 864213』后，查询『我的银行卡密码』，这句话挂在一条
『我喜欢乌龙茶』的结果上返回」。既有锁面把三条腿分开钉（级联清投影 / 来源存活谓词 /
严格血统挂载，见 test_x01_fact_cascade_regression.py 与 test_wanbi_bixiao_smoke.py），
但**没有任何一条把评审的完整流程串起来**：删除 → 走真实 `hybrid_search` 再检索 →
断言已删内容不得从记忆正文、事实挂载、FTS 索引任何一处复活。单点锁测不到腿与腿
的交互（续30/续32 同族教训），本文件补合成路径这一段。

判据防假绿：
- 「删除前可达」前提断言（test 1）：删除前查询必须真能命中秘密（正文或挂载）——
  环境变了、召不回，就判 INCONCLUSIVE 红给人看，绝不让「删除后召不回」在
  「本来就召不回」的空环境上白捡绿。
- 三处一致性单列成 test 2：SQL 行、FTS 行、向量 id 各自独立断言——任何一处
  同步失败都能点名归因，不与检索面互为掩护（promoter.py delete_memory 文档
  承诺「三处保持一致」，此前 FTS 半边无人钉：既有级联测试只核 SQL+向量）。

隔离（AGENTS.md 测试纪律）：内存真库 + 真 FTS5；只替身向 LLM 外部件
（intent/embed）与向量库（同款 VS 桩，照抄 test_x01_fact_cascade_regression.py 的
env fixture）；检索/删除/挂载/守卫逻辑本体一律真跑、零 mock。engine 经 monkeypatch
重绑，夹具结束自动复原，绝不触碰宿主 LANTAI_HOME/真实库。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.time import utcnow
from lantai.evolution.promoter import delete_memory
from lantai.models.tables import FactAtomic, FactEntity, MemoryItem
from lantai.storage.fts import fts_has_row, init_fts, sync_fts

SECRET = "我的银行卡密码是 864213"
# 评审里的乌龙茶行 + 一个对「银行卡密码」真实可命中的正文（保证删除后它仍是首位
# 结果——挂载断言要有活靶子，不能在零结果上假绿）
TEA = "我喜欢乌龙茶，也聊过我的银行卡"
QUERY = "我的银行卡密码"


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())
    monkeypatch.setattr(db_module, "engine", engine)

    class VS:
        def __init__(self):
            self.deleted = []

        def delete(self, ids):
            self.deleted.extend(ids)

        def search(self, *a, **k):
            return []

        def add(self, *a, **k):
            return None

        def has(self, ids):
            return False

    vs = VS()
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: vs)
    yield engine, vs
    engine.dispose()


def _seed_mem(engine, mid, content):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=content,
                lane="general",
                user_id=None,
                status="active",
                importance=0.9,
                decay_score=1.0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()
        sync_fts(s, mid, content)
        s.commit()


def _seed_fact(engine, fid, statement, source):
    with Session(engine) as s:
        s.add(
            FactAtomic(
                id=fid,
                user_id=None,
                statement=statement,
                predicate="has",
                confidence=0.9,
                source_memory_id=source,
            )
        )
        s.commit()
        s.add(FactEntity(fact_id=fid, entity_name="银行卡", role="subject"))
        s.add(FactEntity(fact_id=fid, entity_name="密码", role="subject"))
        s.commit()


def _hybrid(query):
    """真实检索入口，只替身外部件（intent LLM / embed / 向量面由 env 的 VS 桩接管）。"""
    from lantai.retrieval import hybrid

    with (
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "candidate_n": 5},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
    ):
        return hybrid.hybrid_search(query, top_k=5, use_rerank=False)


def _secret_in(res):
    """检索结果里秘密的每一条出现路径：记忆正文 / 挂载事实语句。"""
    hits = []
    for r in res:
        mem = r.get("memory", {}) or {}
        if SECRET in (mem.get("content") or ""):
            hits.append(("body", mem.get("id")))
        for f in r.get("atomic_facts") or []:
            if SECRET in (f.get("statement") or ""):
                hits.append(("attach", mem.get("id"), f.get("id")))
    return hits


def test_review_replay_deleted_secret_never_resurfaces(env):
    """评审头条重放：删除秘密记忆后，再检索不得从任何一道让它复活。"""
    engine, _ = env
    _seed_mem(engine, "m-tea", TEA)
    _seed_mem(engine, "m-sec", SECRET)
    _seed_fact(engine, "f-sec", SECRET, "m-sec")

    # 前提（防空环境假绿）：删除前必须真的可达——正文命中秘密，或事实挂到任一结果
    pre = _secret_in(_hybrid(QUERY))
    assert pre, (
        f"前提失效：删除前查询 {QUERY!r} 都召不回秘密（{pre=}），"
        "本环境测不到泄漏面，判据无归因价值——修环境，别改断言"
    )

    out = delete_memory("m-sec")
    assert out == {"ok": True}, f"删除路径本身失败，重放无意义：{out}"

    # 级联事实层：主表与实体投影同事务清空（内存库 PRAGMA foreign_keys 默认 OFF，
    # 孤儿只能由被测函数自己消灭——续28 口径，不许靠连接开关假绿）
    with Session(engine) as s:
        assert s.get(FactAtomic, "f-sec") is None, "事实主表行残留"
        assert s.exec(select(FactEntity).where(FactEntity.fact_id == "f-sec")).all() == [], (
            "实体投影孤儿残留：事实反查索引还能命中已删事实"
        )

    # 重放断言：已删秘密不得经正文、挂载或任何结果复活
    post = _hybrid(QUERY)
    leaked = _secret_in(post)
    assert leaked == [], f"X-01 原泄漏形态复发（已删内容重新出现）：{leaked}"
    assert all((r.get("memory", {}) or {}).get("id") != "m-sec" for r in post), (
        "已删记忆行仍出现在结果集"
    )


def test_delete_memory_keeps_sql_fts_vector_consistent(env):
    """delete_memory 文档承诺的三处一致性（promoter.py:757-795）：SQL 行、FTS 行、
    向量 id 三处各自独立钉死——此前既有级联测试只核 SQL+向量，FTS 半边无锁：
    残留的 FTS 行是 BM25/关键词面的复活通道，单靠检索断言会被时间参数/阈值掩护
    溜过去，必须直接点名。"""
    engine, vs = env
    _seed_mem(engine, "m-both", SECRET)
    _seed_fact(engine, "f-both", SECRET, "m-both")
    with Session(engine) as s:
        assert fts_has_row(s, "m-both"), "前提失效：种子没进 FTS，删除半边测不到"

    out = delete_memory("m-both")
    assert out == {"ok": True}

    with Session(engine) as s:
        assert s.get(MemoryItem, "m-both") is None, "① SQL 行残留"
        assert not fts_has_row(s, "m-both"), "② FTS 行残留：sync_fts(None) 未随删执行"
    assert "m-both" in vs.deleted, "③ 向量 id 未删：语义面还能召回已删内容"
