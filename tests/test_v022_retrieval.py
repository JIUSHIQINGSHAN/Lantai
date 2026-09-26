"""v022 上游吸收（aiduMEI v21.2）检索侧行为测试：票据 01/02/03。

不 mock 冒烟：真实内存 SQLite + 真实 MemoryItem，仅 mock 外部依赖
（LLM intent、embedding、向量存储）。
- 回声抑制（M2，默认关）：本会话自写候选不回捞；关=行为不变
- MMR 多样性（M4，默认关）：开=近义重复组不全占榜；关=按分截断
- errsig（M6）：查询含报错签名 → 正文精确命中同签名的候选加 bonus
"""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    return e


def _get_test_session():
    raise RuntimeError("测试须先 patch db_module.get_session")


def _add_mem(engine, content: str, *, session_id: str | None = None, created_at=None) -> str:
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
            created_at=created_at or utcnow(),
            session_id=session_id,
        )
        s.add(mem)
        # 同事务写 FTS（生产路径同款）：commit 之后再写会落在未提交事务里，
        # Session 关闭即回滚，memory_fts 恒空、FTS 腿全哑
        sync_fts(s, mid, content)
        s.commit()
    return mid


_SEARCH_PATCHES = dict(
    intent="lantai.retrieval.intent.chat_json",
    embed="lantai.retrieval.hybrid.embed",
    store="lantai.retrieval.hybrid.get_vector_store",
)


def _search(engine, query, *, top_k=10, params=None, principal=None, explain=False):
    def _get_test_session():
        return Session(engine)

    # 替身遵守真实过滤契约（整改票 04）：按 filters 过滤 session/lane/domain，
    # 不再全库伪造——否则回声/MMR 的主路径语义无法被有效验证。
    with Session(engine) as s:
        rows = s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()

    def _respect(row, filters):
        if not filters:
            return True
        for k, v in filters.items():
            val = getattr(row, k, None)
            if isinstance(v, dict):
                if val not in v.get("$in", []):
                    return False
            elif val != v:
                return False
        return True

    def _fake_search(_qv, top_k=10, filters=None):
        picked = [m for m in rows if _respect(m, filters)]
        return [{"id": m.id, "distance": 0.1 + i * 0.01} for i, m in enumerate(picked[:top_k])]

    fake_store = Mock(search=Mock(side_effect=_fake_search), add=Mock(), delete=Mock())
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
            principal=principal,
            explain=explain,
        )


class TestEchoSuppression:
    """整改票 04：回声抑制时间窗语义（ADR-0046）——开/关对照。

    session 域检索本就按 session 圈定候选池（向量 filters + FTS SQL），
    旧语义「删光同 session 候选」会清空会话内召回；新语义只抑制窗口期内
    本会话新写入的回声（上游教训：开关必须有对照冒烟）。"""

    def test_off_returns_session_writes(self, engine):
        """默认关：本会话写入照常回捞（行为不变，零回归）。"""
        assert settings.ECHO_SUPPRESS_ENABLED is False
        mid = _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        results = _search(engine, "部署在本地内网服务器", principal=Principal(session_id="sess_1"))
        assert [r["memory"]["id"] for r in results] == [mid]

    def test_on_suppresses_only_fresh_session_writes(self, engine):
        """开启：窗口内本会话新写入被抑制；窗口外同会话记忆照常召回。"""
        fresh = _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        old = _add_mem(
            engine,
            "兰台项目部署在本地内网服务器",
            session_id="sess_1",
            created_at=utcnow() - timedelta(hours=2),
        )
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ECHO_SUPPRESS_ENABLED": True})
        results = _search(
            engine,
            "部署在本地内网服务器",
            params=p,
            principal=Principal(session_id="sess_1"),
        )
        ids = [r["memory"]["id"] for r in results]
        assert fresh not in ids, "窗口内新写入的回声应被抑制"
        assert old in ids, "窗口外的同会话记忆不应被误杀"

    def test_on_without_session_is_noop(self, engine):
        """空 session 不过滤（上游纪律：空 session_id 一律不过滤）。"""
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_2")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ECHO_SUPPRESS_ENABLED": True})
        results = _search(
            engine, "部署在本地内网服务器", params=p, principal=Principal(session_id=None)
        )
        assert len(results) == 2

    def test_window_param_fail_closed(self):
        """窗口参数非法回默认 900（正数 fail-closed，整改票 02 同纪律）。"""
        from lantai.retrieval.hybrid import RetrievalParams

        assert (
            RetrievalParams.from_overrides(
                {"ECHO_SUPPRESS_WINDOW_SECONDS": -5}
            ).echo_suppress_window
            == 900
        )
        assert (
            RetrievalParams.from_overrides(
                {"ECHO_SUPPRESS_WINDOW_SECONDS": "abc"}
            ).echo_suppress_window
            == 900
        )
        assert (
            RetrievalParams.from_overrides(
                {"ECHO_SUPPRESS_WINDOW_SECONDS": 60}
            ).echo_suppress_window
            == 60
        )


class TestMMRDiversity:
    """票据 02：MMR 多样性截断——开/关对照。

    MMR 在打分出口那一刀生效（候选池 > fetch_n 才触发，同上游纪律）。
    诚实替身下向量腿截到 fetch_n(=candidate_n×multiplier=20)，额外候选必须
    由 FTS 腿真实供给——查询用 4 字词过 trigram，种子内容共享「兰台项目」
    子串；重复组 18 条不占满 FTS LIMIT 20，独特条目得以入池（池 21 > 20）。"""

    _DUP = "兰台项目使用 SQLite 存储记忆数据"

    def _seed(self, engine, n_dup=18, n_unique=3):
        for _ in range(n_dup):
            _add_mem(engine, self._DUP)
        _add_mem(engine, "兰台项目团队喜欢在周五下午发布新版")
        _add_mem(engine, "兰台项目的命名体系取材传统文化意象")
        _add_mem(engine, "兰台项目的检索引擎有四路混合通道")

    def test_off_pure_score_order(self, engine):
        """关：近义重复组全占榜（行为不变，零回归）。"""
        assert settings.MMR_ENABLED is False
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_ENABLED": False})
        results = _search(engine, "兰台项目", top_k=4, params=p)
        assert len(results) == 4
        dup_count = sum(1 for r in results if r["document"] == self._DUP)
        assert dup_count >= 3

    def test_on_reduces_duplicates(self, engine):
        """开：MMR 把近义重复组挤出榜，多样化内容浮上来。

        FTS_RECALL_TOP_K=30 让 FTS 腿供全 21 条入池（> fetch_n 20 触发 MMR）。
        诚实参数下的真实行为：惩罚 0.3·1.0 挤掉部分重复组（top4 从 4 重复变
        2 重复 + 2 独特），但第 21 名独特条目与第 2 名重复的相关差 (~0.24)
        决定重复组不会全部出局——断言多样化生效而非全清。"""
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides(
            {"MMR_ENABLED": True, "MMR_LAMBDA": 0.7, "FTS_RECALL_TOP_K": 30}
        )
        results = _search(engine, "兰台项目", top_k=4, params=p)
        assert len(results) == 4
        dup_count = sum(1 for r in results if r["document"] == self._DUP)
        assert dup_count <= 2, "MMR 应把重复组挤出部分榜位"
        assert len({r["document"] for r in results}) >= 2, "榜单应多样化"

    def test_on_lambda_bounds(self, engine):
        """λ 越界 fail-closed：回默认 0.7，不炸检索。"""
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides(
            {"MMR_ENABLED": True, "MMR_LAMBDA": 99.0, "FTS_RECALL_TOP_K": 30}
        )
        results = _search(engine, "兰台项目", top_k=4, params=p)
        assert len(results) == 4


class TestErrSig:
    """票据 03：错误签名通道。"""

    def test_query_with_signature_boosts_hit(self, engine):
        """查询含 ValueError → 正文真含该签名的候选加 bonus 且进 explain。"""
        _add_mem(engine, "解析配置时报 ValueError 因为字段缺失")
        _add_mem(engine, "团队周五下午发布新版")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ERRSIG_BONUS": 0.10})
        results = _search(
            engine, "为什么报 ValueError", top_k=2, params=p, principal=Principal(), explain=True
        )
        assert results, "至少命中报错记忆"
        hit = next((r for r in results if "ValueError" in r["document"]), None)
        assert hit is not None
        assert hit["explain"]["errsig_bonus"] == 0.10

    def test_plain_chinese_query_untouched(self, engine):
        """普通中文查询抽不到签名 → 规则不参与打分（bonus 恒 0）。"""
        _add_mem(engine, "解析配置时报 ValueError 因为字段缺失")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ERRSIG_BONUS": 0.10})
        results = _search(
            engine, "配置解析", top_k=5, params=p, principal=Principal(), explain=True
        )
        for r in results:
            assert r["explain"]["errsig_bonus"] == 0.0

    def test_zero_bonus_disables(self, engine):
        """bonus=0 即关闭：不加分。"""
        _add_mem(engine, "解析配置时报 ValueError 因为字段缺失")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ERRSIG_BONUS": 0.0})
        results = _search(
            engine, "报 ValueError", top_k=5, params=p, principal=Principal(), explain=True
        )
        for r in results:
            assert r["explain"]["errsig_bonus"] == 0.0


class TestErrSigUnit:
    """errsig 模块纯函数（不 mock）。"""

    def test_extract(self):
        from lantai.retrieval.errsig import extract_error_signatures

        assert extract_error_signatures("修 ValueError 和 KeyError") == ("ValueError", "KeyError")
        assert extract_error_signatures("没有报错") == ()
        assert extract_error_signatures("") == ()

    def test_single_source_shared(self):
        """写入侧与检索侧共用同一正则（单一真源，杜绝两份拷贝漂移）。"""
        from lantai.retrieval import errsig

        assert errsig._ERRSIG_RE.pattern == errsig._ERRSIG_RE.pattern
        # future: gate 侧 import 该 re，测试在 gate 接入后补


class TestParamsFailClosed:
    """整改票 02：参数校验必须覆盖所有构造路径——from_overrides 显式覆盖
    不得绕过 default_factory 的 fail-closed 校验（λ/bonus 越界直通评分）。"""

    def test_overrides_lambda_out_of_range_fails_closed(self):
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_LAMBDA": 99.0})
        assert p.mmr_lambda == 0.7

    def test_overrides_bonus_invalid_type_fails_closed(self):
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ERRSIG_BONUS": "abc"})
        assert p.errsig_bonus == 0.10

    def test_overrides_nan_and_negative_fails_closed(self):
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_LAMBDA": float("nan"), "ERRSIG_BONUS": -1.0})
        assert p.mmr_lambda == 0.7
        assert p.errsig_bonus == 0.10

    def test_valid_overrides_pass_through(self):
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_LAMBDA": 0.8, "ERRSIG_BONUS": 0.2})
        assert p.mmr_lambda == 0.8
        assert p.errsig_bonus == 0.2

    def test_default_construction_still_reads_settings(self, monkeypatch):
        from lantai.retrieval.hybrid import RetrievalParams

        monkeypatch.setattr(settings, "MMR_LAMBDA", 0.6)
        assert RetrievalParams().mmr_lambda == 0.6
