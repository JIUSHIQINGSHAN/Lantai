"""episode credit 影子接线（票据 D，ADR-0056）——默认关逐位一致回归。

不 mock 冒烟：真实内存 SQLite + 真实 MemoryItem/EpisodeRecord 落库，直调
hybrid_search 全主路径；mock 仅限外部依赖（LLM intent、embedding、向量存储）。
- 默认关：episode 表里有无 credit 数据，检索输出（排序+分数）逐位一致
- 默认关：episode 侧表零查询（取数前短路）
- 显式开：score × (1 + w·tanh(credit))，系数按 ADR-0056 公式精确校验
"""

import math
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, select

import lantai.services.episode_service as episode_service_module
import lantai.storage.db as db_module
from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import EpisodeRecord, EpisodeStep, MemoryItem
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    return e


def _add_mem(engine, content: str) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        mem = MemoryItem(
            id=mid,
            memory_type="general",
            key=mid,
            content=content,
            lane="general",
            status="active",
            importance=0.5,
            use_count=0,
            decay_score=1.0,
            last_used_at=utcnow(),
            created_at=utcnow(),
        )
        s.add(mem)
        sync_fts(s, mid, content)
        s.commit()
    return mid


def _record_episode(engine, session_id: str, outcome: str, memory_id: str):
    def _get_test_session():
        return Session(engine)

    with patch.object(db_module, "get_session", _get_test_session):
        from lantai.services.episode_service import record_episode

        return record_episode(
            session_id=session_id, outcome=outcome, steps=[{"memory_id": memory_id, "rank": 1}]
        )


_SEARCH_PATCHES = dict(
    intent="lantai.retrieval.intent.chat_json",
    embed="lantai.retrieval.hybrid.embed",
    store="lantai.retrieval.hybrid.get_vector_store",
)


def _search(engine, query, *, top_k=10, params=None, explain=False, episode_spy=None):
    def _get_test_session():
        return Session(engine)

    with Session(engine) as s:
        rows = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()

    def _fake_search(_qv, top_k=10, filters=None):
        # 主路径形状：归属过滤由调用方传入，这里不因 filters 非空而清空
        # 候选（否则 hybrid 判零召回走拾遗回退，测不到主路径加权点）。
        return [{"id": m.id, "distance": 0.1 + i * 0.01} for i, m in enumerate(rows[:top_k])]

    fake_store = Mock(search=Mock(side_effect=_fake_search), add=Mock(), delete=Mock())
    if episode_spy is not None:
        with (
            patch.object(db_module, "get_session", _get_test_session),
            patch(_SEARCH_PATCHES["intent"], return_value={"intent": "fact_lookup", "reason": "t"}),
            patch(_SEARCH_PATCHES["embed"], return_value=[[0.1] * 8]),
            patch(_SEARCH_PATCHES["store"], return_value=fake_store),
            patch.object(episode_service_module, "episode_credit_map", episode_spy),
        ):
            from lantai.retrieval import hybrid

            return hybrid.hybrid_search(
                query,
                top_k=top_k,
                use_rerank=False,
                params=params,
                principal=Principal(),
                explain=explain,
            )
    with (
        patch.object(db_module, "get_session", _get_test_session),
        patch(_SEARCH_PATCHES["intent"], return_value={"intent": "fact_lookup", "reason": "t"}),
        patch(_SEARCH_PATCHES["embed"], return_value=[[0.1] * 8]),
        patch(_SEARCH_PATCHES["store"], return_value=fake_store),
    ):
        from lantai.retrieval import hybrid

        return hybrid.hybrid_search(
            query,
            top_k=top_k,
            use_rerank=False,
            params=params,
            principal=Principal(),
            explain=explain,
        )


def _strip_explain(results):
    """explain 里的 episode_credit 键在关/开两态都存在，比较主干即可。"""
    return [
        {"score": r["score"], "id": r["memory"]["id"], "document": r["document"]} for r in results
    ]


class TestEpisodeCreditShadow:
    def test_default_off(self):
        assert settings.EPISODE_CREDIT_ENABLED is False

    def test_disabled_bitwise_identical_with_and_without_episode_data(self, engine):
        """默认关（显式构造）：episode 表有/无 credit 数据，输出逐位一致。

        时钟冻结（_age_multiplier 经 lantai.core.time.utcnow 取当前时刻）：
        不冻结的话两次调用间隔几毫秒，衰减乘子在 1e-10 位漂移——那是时间
        噪音不是 episode 影响，逐位一致的主张必须在冻结时钟下验证。
        """
        from lantai.core.time import utcnow as real_utcnow

        a = _add_mem(engine, "兰台项目的检索引擎有四路混合通道")
        b = _add_mem(engine, "兰台项目团队喜欢在周五下午发布新版")
        _record_episode(engine, "sess_1", "success", a)  # credit=1.0
        _record_episode(engine, "sess_2", "failure", b)  # credit=-1.0
        # 冻结点必须在数据写入之后：valid_from 晚于冻结点会被时间视图
        # 当作「尚未生效」过滤掉（调试实证：取在写库前则检索零结果）。
        frozen = real_utcnow()

        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"EPISODE_CREDIT_ENABLED": False})
        with patch("lantai.core.time.utcnow", return_value=frozen):
            with_eps = _search(engine, "兰台项目", top_k=5, params=p)

        with Session(engine) as s:
            for stmt in (delete(EpisodeStep), delete(EpisodeRecord)):
                s.exec(stmt)
            s.commit()
        with patch("lantai.core.time.utcnow", return_value=frozen):
            without_eps = _search(engine, "兰台项目", top_k=5, params=p)

        assert _strip_explain(with_eps) == _strip_explain(without_eps), (
            "开关关闭时 episode 数据不得影响排序与分数"
        )
        assert {r["memory"]["id"] for r in with_eps} == {a, b}

    def test_disabled_zero_episode_queries(self, engine):
        """默认关：episode 侧表零查询（取数前短路，无额外开销）。"""
        _add_mem(engine, "兰台项目的检索引擎有四路混合通道")
        calls = []

        def _spy(memory_ids=None):
            calls.append(list(memory_ids or []))
            return {}

        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"EPISODE_CREDIT_ENABLED": False})
        _search(engine, "兰台项目", top_k=5, params=p, episode_spy=_spy)
        assert calls == [], "开关关闭时不得读取 episode 表"

    def test_enabled_applies_adr0056_formula(self, engine):
        """显式开：score × (1 + w·tanh(credit))，w=0.1、credit 精确校验（冻结时钟）。"""
        from lantai.core.time import utcnow as real_utcnow

        a = _add_mem(engine, "兰台项目的检索引擎有四路混合通道")
        _record_episode(engine, "sess_1", "success", a)  # 单步 credit=1.0
        frozen = real_utcnow()  # 取在写库后：valid_from 不得晚于冻结点（否则被时间视图滤掉）

        from lantai.retrieval.hybrid import RetrievalParams

        p_off = RetrievalParams.from_overrides({"EPISODE_CREDIT_ENABLED": False})
        p_on = RetrievalParams.from_overrides({"EPISODE_CREDIT_ENABLED": True})
        with patch("lantai.core.time.utcnow", return_value=frozen):
            base = _search(engine, "兰台项目", top_k=5, params=p_off, explain=True)
            boosted = _search(engine, "兰台项目", top_k=5, params=p_on, explain=True)

        base_score = next(r["score"] for r in base if r["memory"]["id"] == a)
        hit = next(r for r in boosted if r["memory"]["id"] == a)
        credit = hit["explain"]["episode_credit"]
        assert credit == 1.0
        assert hit["score"] == pytest.approx(
            base_score * (1.0 + 0.1 * math.tanh(credit)), rel=1e-12
        ), "加权公式必须与 ADR 一致（系数 0.1）"

    def test_weight_fail_closed(self):
        """weight 越界 fail-closed 回默认 0.1。"""
        from lantai.retrieval.hybrid import RetrievalParams

        assert (
            RetrievalParams.from_overrides({"EPISODE_CREDIT_WEIGHT": 5.0}).episode_credit_weight
            == 0.1
        )
        assert (
            RetrievalParams.from_overrides({"EPISODE_CREDIT_WEIGHT": "abc"}).episode_credit_weight
            == 0.1
        )
        assert (
            RetrievalParams.from_overrides({"EPISODE_CREDIT_WEIGHT": 0.2}).episode_credit_weight
            == 0.2
        )
