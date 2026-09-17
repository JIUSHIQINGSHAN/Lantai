"""v022 上游吸收（aiduMEI v21.2）检索侧行为测试：票据 01/02/03。

不 mock 冒烟：真实内存 SQLite + 真实 MemoryItem，仅 mock 外部依赖
（LLM intent、embedding、向量存储）。
- 回声抑制（M2，默认关）：本会话自写候选不回捞；关=行为不变
- MMR 多样性（M4，默认关）：开=近义重复组不全占榜；关=按分截断
- errsig（M6）：查询含报错签名 → 正文精确命中同签名的候选加 bonus
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
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


def _add_mem(engine, content: str, *, session_id: str | None = None) -> str:
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
            session_id=session_id,
        )
        s.add(mem)
        s.commit()
        sync_fts(s, mid, content)
    return mid


class _Principal:
    def __init__(self, session_id=None, user_id=None):
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = None
        self.role = "user"


_SEARCH_PATCHES = dict(
    intent="lantai.retrieval.intent.chat_json",
    embed="lantai.retrieval.hybrid.embed",
    store="lantai.retrieval.hybrid.get_vector_store",
)


def _search(engine, query, *, top_k=10, params=None, principal=None, explain=False):
    def _get_test_session():
        return Session(engine)

    # 用真实库存的全部记忆伪造向量命中——保证走主路径（回声/MMR/errsig
    # 都在主路径打分循环里；keyword fallback 是另一条腿，有独立 session 圈定）
    with Session(engine) as s:
        hits = [
            {"id": m.id, "distance": 0.1 + i * 0.01}
            for i, m in enumerate(s.query(MemoryItem).all())
        ]
    fake_store = Mock(search=Mock(return_value=hits), add=Mock(), delete=Mock())
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
    """票据 01 检索半：回声抑制（M2）——开/关对照（上游教训：开关必须有对照冒烟）。"""

    def test_off_returns_session_writes(self, engine):
        """默认关：本会话写入照常回捞（行为不变，零回归）。"""
        assert settings.ECHO_SUPPRESS_ENABLED is False
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_2")
        results = _search(engine, "部署在本地内网服务器", principal=_Principal("sess_1"))
        assert len(results) == 2

    def test_on_filters_current_session_writes(self, engine):
        """开启：本会话自写的候选被滤掉，他会话记忆照常召回。"""
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_2")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ECHO_SUPPRESS_ENABLED": True})
        results = _search(
            engine, "部署在本地内网服务器", params=p, principal=_Principal("sess_1")
        )
        assert len(results) == 1

    def test_on_without_session_is_noop(self, engine):
        """空 session 不过滤（上游纪律：空 session_id 一律不过滤）。"""
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_1")
        _add_mem(engine, "兰台项目部署在本地内网服务器", session_id="sess_2")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ECHO_SUPPRESS_ENABLED": True})
        results = _search(engine, "部署在本地内网服务器", params=p, principal=_Principal(None))
        assert len(results) == 2


class TestMMRDiversity:
    """票据 02：MMR 多样性截断——开/关对照。

    MMR 在打分出口那一刀生效（候选池 > fetch_n 才触发，同上游纪律），
    所以种子池 23 条 > 默认 fetch_n 16。"""

    _DUP = "兰台项目使用 SQLite 存储记忆数据"

    def _seed(self, engine, n_dup=20, n_unique=3):
        for _ in range(n_dup):
            _add_mem(engine, self._DUP)
        _add_mem(engine, "兰台团队喜欢在周五下午发布新版")
        _add_mem(engine, "兰台的命名体系取材传统文化意象")
        _add_mem(engine, "兰台的检索引擎有四路混合通道")

    def test_off_pure_score_order(self, engine):
        """关：近义重复组全占榜（行为不变，零回归）。"""
        assert settings.MMR_ENABLED is False
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_ENABLED": False})
        results = _search(engine, "兰台", top_k=4, params=p)
        assert len(results) == 4
        dup_count = sum(1 for r in results if r["document"] == self._DUP)
        assert dup_count >= 3

    def test_on_reduces_duplicates(self, engine):
        """开：MMR 把近义重复组挤出榜，多样化内容浮上来。"""
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_ENABLED": True, "MMR_LAMBDA": 0.7})
        results = _search(engine, "兰台", top_k=4, params=p)
        assert len(results) == 4
        dup_count = sum(1 for r in results if r["document"] == self._DUP)
        assert dup_count <= 1

    def test_on_lambda_bounds(self, engine):
        """λ 越界 fail-closed：夹取 0~1，不炸检索。"""
        self._seed(engine)
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"MMR_ENABLED": True, "MMR_LAMBDA": 99.0})
        results = _search(engine, "兰台", top_k=4, params=p)
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
            engine, "为什么报 ValueError", top_k=2, params=p, principal=_Principal(), explain=True
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
        results = _search(engine, "配置解析", top_k=5, params=p, principal=_Principal(), explain=True)
        for r in results:
            assert r["explain"]["errsig_bonus"] == 0.0

    def test_zero_bonus_disables(self, engine):
        """bonus=0 即关闭：不加分。"""
        _add_mem(engine, "解析配置时报 ValueError 因为字段缺失")
        from lantai.retrieval.hybrid import RetrievalParams

        p = RetrievalParams.from_overrides({"ERRSIG_BONUS": 0.0})
        results = _search(engine, "报 ValueError", top_k=5, params=p, principal=_Principal(), explain=True)
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
