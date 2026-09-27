"""FTS5 集成测试：同事务同步 + 检索融合 + 追加召回 + BM25 缓存"""

import sqlite3
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage.fts import init_fts, search_fts, sync_fts


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
        s.add(
            MemoryItem(
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
        )
        s.commit()
    return mid


def test_sync_fts_same_transaction(engine):
    """sync_fts 与记忆写入同事务：commit 后 FTS 可见"""
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="general",
                key=mid,
                content="用户喜欢喝咖啡",
                lane="general",
                status="active",
                importance=0.5,
                use_count=0,
                decay_score=1.0,
                last_used_at=utcnow(),
                created_at=utcnow(),
            )
        )
        sync_fts(s, mid, "用户喜欢喝咖啡")
        s.commit()
    with engine.connect() as conn:
        assert mid in search_fts(conn.connection.driver_connection, "喝咖啡")


def test_sync_fts_update(engine):
    """同 id 再同步 = 覆盖（先删后插）"""
    mid = _add_mem(engine, "旧内容")
    with Session(engine) as s:
        sync_fts(s, mid, "新内容")
        s.commit()
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        assert mid in search_fts(c, "新内容")
        assert mid not in search_fts(c, "旧内容")


def test_special_chars_no_syntax_error(engine):
    """FTS5 MATCH 特殊字符（= @ . ? /）不再触发 syntax error（引号转义）"""
    mid = _add_mem(engine, "E=MC2 是质能方程，物理常用")
    with Session(engine) as s:
        sync_fts(s, mid, "E=MC2 是质能方程，物理常用")
        s.commit()
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        # 含特殊字符的查询：修复前 FTS5 syntax error → 通道降级；修复后正常召回
        assert mid in search_fts(c, "E=MC2 质能方程")  # trigram 最小 3 字符
        # 碎片符号查询：不抛异常（返回列表，内容不含则不命中）
        assert isinstance(search_fts(c, "物理 @ 常用 . 查询 ? 测试"), list)


def test_short_token_does_not_poison_and(engine):
    """2 字词（trigram 最小 3 字符）不得毒化 AND 链：「API 密钥」类查询仍命中。

    修复前 `"API" AND "密钥"`：密钥 2 字符在 trigram 索引侧无法成词，
    整条 MATCH 返回空 → 中文+ASCII 混合查询整体失效（评测集 superseded 用例暴露）。
    """
    mid = _add_mem(engine, "API 密钥存储在 config.py")
    with Session(engine) as s:
        sync_fts(s, mid, "API 密钥存储在 config.py")
        s.commit()
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        assert mid in search_fts(c, "API 密钥")


def test_supersedes_demotes_old_below_new(engine):
    """supersedes 边降权：被取代旧值在新值同在候选集时排到新值之后。

    API 密钥 场景：修复前 BM25 空格 token 计数让旧值（config.py）确定性排前；
    降权后必须新值（环境变量注入）在前——宁 miss 不脏写：旧值不删、仍在结果中。
    """
    from lantai.core.ids import new_id

    old = _add_mem(engine, "API 密钥存储在 config.py")
    new = _add_mem(engine, "API 密钥改为环境变量注入")
    with Session(engine) as s:
        sync_fts(s, old, "API 密钥存储在 config.py")
        sync_fts(s, new, "API 密钥改为环境变量注入")
        s.add(
            MemoryEdge(
                id=new_id("edge"),
                source_memory_id=new,
                target_memory_id=old,
                relation="supersedes",
                confidence=1.0,
            )
        )
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
        ),
    ):
        from lantai.retrieval import hybrid

        results = hybrid.hybrid_search("API 密钥", top_k=5, use_rerank=False)
    ids = [r["memory"]["id"] for r in results]
    assert old in ids and new in ids  # 不删旧值（宁 miss 不脏写）
    assert ids.index(new) < ids.index(old)  # 新值必须在前


def test_query_symbols_only_does_not_crash(engine):
    """纯符号/碎片查询不抛异常，返回空列表而非降级日志刷屏"""
    _add_mem(engine, "普通内容记忆")
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        assert search_fts(c, "@ . = ? /") == []
        assert search_fts(c, "???") == []


def test_query_embedded_quotes_escaped(engine):
    """查询内含双引号不破坏 MATCH 语法（FTS5 用 "" 转义）"""
    mid = _add_mem(engine, '用户说"明天见"后离开')
    with Session(engine) as s:
        sync_fts(s, mid, '用户说"明天见"后离开')
        s.commit()
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        assert mid in search_fts(c, '"明天见"')


def test_sync_fts_delete(engine):
    """content=None 删除索引"""
    mid = _add_mem(engine, "要删除的记忆")
    with Session(engine) as s:
        sync_fts(s, mid, None)
        s.commit()
    with engine.connect() as conn:
        assert mid not in search_fts(conn.connection.driver_connection, "要删除的记忆")


def test_hybrid_fts_extra_recall(engine):
    """FTS 命中但向量未命中的记忆被追加召回"""
    from lantai.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "向量命中的记忆")
    fts_only_id = _add_mem(engine, "咖啡因摄入记录")
    # 向量只返回 vec_hit；fts 用真实表
    with Session(engine) as s:
        sync_fts(s, fts_only_id, "咖啡因摄入记录")
        sync_fts(s, vec_hit_id, "向量命中的记忆")
        s.commit()

    def get_test_session():
        return Session(engine)

    def fake_store(ids):
        return Mock(
            search=Mock(return_value=[{"id": i, "distance": 0.3, "metadata": {}} for i in ids])
        )

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=fake_store([vec_hit_id])),
    ):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)
    ids = {r["memory"]["id"] for r in results}
    assert vec_hit_id in ids
    assert fts_only_id in ids  # 追加召回生效


def test_supersedes_explain_marks_demotion(engine):
    """检索透明：supersedes 降权在 explain 里标注 superseded_by + demoted（可审计）。"""
    from lantai.core.ids import new_id

    old = _add_mem(engine, "API 密钥存储在 config.py")
    new = _add_mem(engine, "API 密钥改为环境变量注入")
    with Session(engine) as s:
        sync_fts(s, old, "API 密钥存储在 config.py")
        sync_fts(s, new, "API 密钥改为环境变量注入")
        s.add(
            MemoryEdge(
                id=new_id("edge"),
                source_memory_id=new,
                target_memory_id=old,
                relation="supersedes",
                confidence=1.0,
            )
        )
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
        ),
    ):
        from lantai.retrieval import hybrid

        results = hybrid.hybrid_search("API 密钥", top_k=5, use_rerank=False, explain=True)
    by_id = {r["memory"]["id"]: r for r in results}
    assert by_id[old]["explain"].get("demoted") is True
    assert new in by_id[old]["explain"].get("superseded_by", [])
    # 新值本身未被降权
    assert by_id[new]["explain"].get("demoted") is not True


def test_hybrid_vector_empty_falls_back_to_fts(engine):
    """向量检索为空（空库/embedding 降级）→ FTS5+BM25 兜底，而非零召回"""
    from lantai.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
    ):  # 向量空
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)
    assert results, "向量为空时不应零召回"
    ids = {r["memory"]["id"] for r in results}
    assert fts_id in ids  # FTS 兜底命中


def test_hybrid_vector_empty_trace_marks_fallback(engine):
    """trace=True 时兜底路径记录 fallback_fts 步骤"""
    from lantai.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
    ):
        results, trace_steps = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False, trace=True)
    assert any(s["step"] == "fallback_fts" for s in trace_steps)
    assert fts_id in {r["memory"]["id"] for r in results}


def test_hybrid_explain_breakdown(engine):
    """explain=True → 每条结果附带完整分项（向量路径）"""
    from lantai.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, vec_hit_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(
                search=Mock(return_value=[{"id": vec_hit_id, "distance": 0.3, "metadata": {}}])
            ),
        ),
    ):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False, explain=True)
    assert results
    expl = results[0]["explain"]
    for key in (
        "vector",
        "bm25",
        "fts",
        "decay",
        "lane_boost",
        "final",
        "decay_class",
        "decay_multiplier",
    ):
        assert key in expl, f"missing explain key: {key}"
    assert expl["decay_class"] == "episodic"
    assert expl["decay_multiplier"] == 1.0  # 刚写入，未老化


def test_hybrid_explain_rerank_keeps_breakdown(engine):
    """reranker 开启时 explain 仍保留原始分项（重排前后可对比）"""
    from lantai.retrieval import hybrid

    vec_hit_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, vec_hit_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    fake_rerank = Mock(return_value=[{"score": 0.9, "document": "咖啡因摄入记录"}])
    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(
                search=Mock(return_value=[{"id": vec_hit_id, "distance": 0.3, "metadata": {}}])
            ),
        ),
        patch.object(hybrid.settings, "RERANKER_ENABLED", True),
        patch("lantai.retrieval.hybrid.rerank", fake_rerank),
    ):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=True, explain=True)
    assert results
    assert "document" in results[0]
    assert results[0]["explain"] is not None
    assert results[0]["explain"]["final"] > 0
    assert results[0]["explain"]["decay_class"] == "episodic"


def test_hybrid_explain_fallback(engine):
    """向量空降级路径 + explain → vector 分项为 0.0，其余齐全"""
    from lantai.retrieval import hybrid

    fts_id = _add_mem(engine, "咖啡因摄入记录")
    with Session(engine) as s:
        sync_fts(s, fts_id, "咖啡因摄入记录")
        s.commit()

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
    ):
        results = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False, explain=True)
    assert results
    expl = results[0]["explain"]
    assert expl["vector"] == 0.0
    assert expl["final"] > 0
    assert expl["decay_class"] == "episodic"


def test_hybrid_vector_empty_no_candidates_returns_empty(engine):
    """兜底路径也无 FTS 候选 → 返回空（不炸）"""
    from lantai.retrieval import hybrid

    def get_test_session():
        return Session(engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
    ):
        results = hybrid.hybrid_search("完全不存在的记忆关键词xyz", top_k=5, use_rerank=False)
    assert results == []


# ── 票06：BM25 OR 路径 3-gram 滑窗分词（词中错字/词面重叠改写的部分匹配兜底）──


def _add_mem_with_fts(engine, content: str) -> str:
    mid = _add_mem(engine, content)
    with Session(engine) as s:
        sync_fts(s, mid, content)
        s.commit()
    return mid


def test_search_fts_bm25_gram_typo_mid_hits(engine):
    """词中错字：错字只污染个别 3-gram，其余滑窗仍命中（AND 路径为 0 的场景）。"""
    from lantai.storage.fts import search_fts_bm25

    mid = _add_mem_with_fts(engine, "机器学习用于图像识别与自然语言处理")
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        rows = search_fts_bm25(c, "机器学习用于图象识别", top_k=5)
    assert mid in [r[0] for r in rows]


def test_search_fts_bm25_gram_paraphrase_word_overlap_hits(engine):
    """词面重叠改写：共享 3-gram（数据库）即部分命中；AND 路径整句短语为 0 的场景。"""
    from lantai.storage.fts import search_fts_bm25

    mid = _add_mem_with_fts(engine, "数据库使用SQLite存储")
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        rows = search_fts_bm25(c, "公司的数据库引擎用的什么", top_k=5)
    assert mid in [r[0] for r in rows]


def test_search_fts_bm25_gram_off_falls_back_to_phrase(engine, monkeypatch):
    """off 对照（铁律 2）：开关关 → 旧整句短语语义，改写零命中。"""
    from lantai.storage.fts import search_fts_bm25

    monkeypatch.setattr("lantai.core.settings.settings.FTS_BM25_GRAM_TOKENIZE", False)
    mid = _add_mem_with_fts(engine, "机器学习用于图像识别与自然语言处理")
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        rows = search_fts_bm25(c, "机器学习用于图象识别", top_k=5)
    assert mid not in [r[0] for r in rows]


def test_search_fts_and_path_shared_segment_hits(engine):
    """AND 路径：连续片段查询可命中（评测集去首字模式依赖）；完全改写零命中。

    现状整改票04 如实声明：默认开档下本路径关键词同为 3-gram，「各 gram 全
    命中」——本测试的片段查询与改写零命中在旧短语语义与新 gram 语义下均成立，
    锚定的是两种语义共有的行为面，不区分新旧语义。
    """
    mid = _add_mem_with_fts(engine, "机器学习用于图像识别与自然语言处理")
    with engine.connect() as conn:
        c = conn.connection.driver_connection
        rows = search_fts(c, "器学习用于图像识别", top_k=5)
    assert mid in rows
    # 完全改写查询零命中（AND 全 gram 命中不成立，召回由 OR 路径补）
    rows2 = search_fts(c, "公司的数据库引擎用的什么", top_k=5)
    assert rows2 == []


class TestRetrievalSilentFailureIsLogged:
    """检索链路三处 `except Exception: pass` 必须改为 warning 留痕。

    背景（`.scratch/retrieval-silent-failure/issues/01-*.md`）：
    hybrid.py:514（主路径 BM25 通道）、:849（_keyword_fallback FTS 命中）、
    :888（_keyword_fallback LIKE 兜底）三处吞掉失败，无任何日志。
    用户可感知的唯一现象是 search 返回空——而「索引坏了」与「确实没存过」
    在返回值和 retrieval_event.zero_result 上完全同形。

    最危险组合：fts.py:31 的 init_fts 吞掉建表失败（仅启动时一条 warning），
    之后每次查询撞 no such table: memory_fts → :849 → :888 → 永久静默空结果。

    口径：只加留痕，不改降级行为（检索失败仍不抛给调用方，向量→关键词→LIKE
    的降级顺序是正确设计）。既有向量失败已有留痕（hybrid.py:438），
    这三处是同文件内的疏漏。
    """

    @staticmethod
    def _seed(engine, content="咖啡因摄入记录"):
        from lantai.storage.fts import sync_fts

        mid = _add_mem(engine, content)
        with Session(engine) as s:
            sync_fts(s, mid, content)
            s.commit()
        return mid

    def test_bm25_channel_failure_logs_warning_and_still_returns(self, engine, caplog):
        """:514 主路径 BM25 通道失败 → warning 留痕 + 检索仍返回（降级不炸）。

        注意：必须让向量通道**成功**（返回一条命中）才能走到 :514 所在的
        主混合路径；向量空则 hybrid_search 会转投 _keyword_fallback，
        测的就不是这一处了。
        """
        import logging

        from lantai.retrieval import hybrid

        fts_id = self._seed(engine)

        with (
            patch.object(db_module, "get_session", lambda: Session(engine)),
            patch(
                "lantai.retrieval.intent.chat_json",
                return_value={"intent": "fact_lookup", "reason": "test"},
            ),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[{"id": fts_id, "distance": 0.1}])),
            ),
            patch(
                "lantai.retrieval.hybrid.search_fts_bm25",
                side_effect=sqlite3.OperationalError("no such table: memory_fts"),
            ),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            results, trace_steps = hybrid.hybrid_search(
                "咖啡因", top_k=5, use_rerank=False, trace=True
            )

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "BM25 通道失败必须留痕，不得静默 pass"
        joined = " | ".join(str(r.getMessage()) for r in warnings)
        assert "bm25" in joined.lower(), f"warning 须点明是 BM25 通道，实得: {joined}"
        # 行为不变：向量通道仍命中（BM25 挂了不连带炸掉整次检索）
        assert fts_id in {r["memory"]["id"] for r in results}

    def test_fts_channel_failure_in_fallback_logs_warning(self, engine, caplog):
        """:849 _keyword_fallback 的 FTS 通道失败 → warning 留痕。"""
        import logging

        from lantai.retrieval import hybrid

        self._seed(engine)

        with (
            patch.object(db_module, "get_session", lambda: Session(engine)),
            patch(
                "lantai.retrieval.intent.chat_json",
                return_value={"intent": "fact_lookup", "reason": "test"},
            ),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[])),
            ),
            patch(
                "lantai.retrieval.hybrid.search_fts",
                side_effect=sqlite3.OperationalError("no such table: memory_fts"),
            ),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "FTS 通道失败必须留痕，不得静默 pass"

    def test_like_fallback_failure_logs_warning(self, engine, caplog):
        """:888 LIKE 兜底失败 → warning 留痕（最后一道兜底失守更要可见）。

        构造：向量空 → 进 _keyword_fallback；BM25/FTS 均返回空（不抛），
        使代码走到 LIKE 分支；再让 session.exec 抛错触发第三处 except。
        """
        import logging

        from lantai.retrieval import hybrid

        self._seed(engine)

        real_session_cls = Session

        class BoomSession(real_session_cls):
            def exec(self, *a, **kw):
                raise sqlite3.OperationalError("database is locked")

        with (
            patch.object(db_module, "get_session", lambda: BoomSession(engine)),
            patch(
                "lantai.retrieval.intent.chat_json",
                return_value={"intent": "fact_lookup", "reason": "test"},
            ),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[])),
            ),
            patch("lantai.retrieval.hybrid.search_fts_bm25", return_value=[]),
            patch("lantai.retrieval.hybrid.search_fts", return_value=[]),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "LIKE 兜底失败必须留痕，不得静默 pass"
        joined = " | ".join(str(r.getMessage()) for r in warnings)
        assert "like" in joined.lower(), f"warning 须点明是 LIKE 兜底，实得: {joined}"

    def test_healthy_retrieval_has_no_warning(self, engine, caplog):
        """反例护栏：正常检索（各通道均成功）不得有 warning——留痕不能变噪音。"""
        import logging

        from lantai.retrieval import hybrid

        fts_id = self._seed(engine)

        with (
            patch.object(db_module, "get_session", lambda: Session(engine)),
            patch(
                "lantai.retrieval.intent.chat_json",
                return_value={"intent": "fact_lookup", "reason": "test"},
            ),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[])),
            ),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            results, _ = hybrid.hybrid_search("咖啡因", top_k=5, use_rerank=False, trace=True)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f"正常检索不应有 warning，实得: {[r.getMessage() for r in warnings]}"
        assert fts_id in {r["memory"]["id"] for r in results}


class TestInitFtsAvailability:
    """`init_fts` 的失败必须可见，且「成功」的声称必须诚实（票 .scratch/fts-availability/01）。

    背景：原实现把建表失败降级成一行启动 warning，调用方 `init_db` 不看返回值，
    `/health` 无条件返回 ok——形成完整静默链。更阴的是 `CREATE VIRTUAL TABLE
    IF NOT EXISTS` 对「已存在但类型/分词器不对」是**静默 no-op**：SQLite 不报错，
    列名检查也过，于是日志照样打 `FTS5 + trigram initialized`（假成功）。

    实测两种坏态：
    - 普通同名表：之后每次查询 `no such column: memory_fts` → 词汇召回全废
    - FTS5 但 tokenize=unicode61：bm25 查询照样成功，只是中文子串召回永久失效，
      任何一层都不报错

    修法口径：`init_fts` 返回 bool，CREATE 之后**核验 sqlite_master 里的真实定义**
    （IF NOT EXISTS 跳过验证，必须自己核），失败 logger.error 留痕。
    不抛异常——抛出去会连坐整个服务启动，而这是「降级但可用」状态。
    """

    @staticmethod
    def _raw_conn():
        import tempfile

        # 用文件库而非 :memory:：DDL 后要另开连接读 sqlite_master，
        # 同一连接的未提交状态会干扰断言（且 init_fts 内部会 commit）
        fd, path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        return sqlite3.connect(path), path

    def test_plain_table_with_same_columns_is_detected(self, caplog):
        """坏态一：同名普通表（列名与正确版一致）→ 必须判不可用，不得假成功。"""
        import logging
        import os

        conn, path = self._raw_conn()
        try:
            conn.execute("CREATE TABLE memory_fts (memory_id TEXT, content TEXT)")
            conn.commit()
            with caplog.at_level(logging.ERROR, logger="lantai"):
                ok = init_fts(conn)
            assert ok is False, (
                "普通同名表会让 CREATE ... IF NOT EXISTS 静默 no-op，"
                "此后每次查询 no such column——必须如实判不可用"
            )
            assert any("memory_fts" in r.getMessage() for r in caplog.records), (
                "须留痕点明是哪张表不可用"
            )
        finally:
            conn.close()
            os.unlink(path)

    def test_wrong_tokenizer_is_detected(self, caplog):
        """坏态二：FTS5 但 tokenize 不是 trigram → 必须判不可用（中文召回已废）。"""
        import logging
        import os

        conn, path = self._raw_conn()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE memory_fts USING fts5("
                "memory_id UNINDEXED, content, tokenize='unicode61')"
            )
            conn.commit()
            with caplog.at_level(logging.ERROR, logger="lantai"):
                ok = init_fts(conn)
            assert ok is False, "分词器不对时中文子串召回已永久失效，不得报成功"
        finally:
            conn.close()
            os.unlink(path)

    def test_creation_failure_returns_false_and_logs(self, caplog):
        """坏态三：建表直接抛异常（如 FTS5 未编译进 sqlite）→ False + error 留痕。

        注意：`sqlite3.Connection.execute` 是只读槽位，patch.object 会在 teardown
        时炸 AttributeError。故用薄包装对象注入失败（真实连接仍在底层）。
        """
        import logging
        import os

        conn, path = self._raw_conn()

        class BoomConn:
            """前两次 execute 正常（PRAGMA + 无 DROP），CREATE 那一次抛。"""

            def __init__(self, inner):
                self._inner = inner
                self._n = 0

            def execute(self, *a, **kw):
                self._n += 1
                if self._n >= 2:  # 第 1 次 PRAGMA table_info，第 2 次即 CREATE
                    raise sqlite3.OperationalError("no such module: fts5")
                return self._inner.execute(*a, **kw)

            def commit(self):
                return self._inner.commit()

        try:
            with caplog.at_level(logging.ERROR, logger="lantai"):
                ok = init_fts(BoomConn(conn))
            assert ok is False
            errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert errors, "建表失败必须 error 留痕（不是一条滚过去的 warning）"
        finally:
            conn.close()
            os.unlink(path)

    def test_healthy_db_returns_true_and_no_error(self, caplog):
        """回归护栏：健康库 → True，且不误报 error。"""
        import logging
        import os

        conn, path = self._raw_conn()
        try:
            with caplog.at_level(logging.ERROR, logger="lantai"):
                ok = init_fts(conn)
            assert ok is True
            assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        finally:
            conn.close()
            os.unlink(path)

    def test_legacy_schema_still_recreated(self):
        """既有行为不变：无 memory_id 列的旧表仍被 DROP 重建，最终可用。"""
        import os

        conn, path = self._raw_conn()
        try:
            conn.execute("CREATE TABLE memory_fts (foo TEXT)")
            conn.commit()
            ok = init_fts(conn)
            assert ok is True
            row = conn.execute("SELECT sql FROM sqlite_master WHERE name='memory_fts'").fetchone()
            assert "trigram" in (row[0] or "")
        finally:
            conn.close()
            os.unlink(path)

    def test_idempotent_second_call_still_true(self):
        """幂等：已初始化的库再调一次仍 True（36 个测试调用点的前提）。"""
        import os

        conn, path = self._raw_conn()
        try:
            assert init_fts(conn) is True
            assert init_fts(conn) is True
        finally:
            conn.close()
            os.unlink(path)


class TestHealthDeepReportsFts:
    """`/health/deep` 必须单独探 FTS，不得只查 sqlite/chromadb 就报 ok。

    背景：FTS 坏的表现是「检索结果变少」——与 sqlite/chromadb 探活完全正交。
    原 `/health/deep` 三项全过也照样返回 ok:true，用户据此认为系统健康
    （票 .scratch/fts-availability/01）。
    """

    @staticmethod
    def _client(engine):
        """内存库 TestClient（db.get_session 指向 engine）。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app

        return TestClient(app)

    def test_healthy_fts_reports_ok(self, engine, monkeypatch):
        """回归护栏：FTS 正常 → checks["fts"] == "ok"，整体 ok 不变 false。"""
        with (
            patch.object(db_module, "get_session", lambda: Session(engine)),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
        ):
            client = self._client(engine)
            resp = client.get("/health/deep")
        data = resp.json()
        assert data["checks"]["fts"] == "ok", f"实得 {data['checks'].get('fts')}"

    def test_broken_fts_makes_deep_health_fail(self):
        """坏态：memory_fts 不是 FTS5/trigram 表 → fts 项 fail 且整体 ok 为 False。"""
        import os
        import tempfile

        # 自建库：先塞一张同名普通表，再建其余表（模拟真实坏库）
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        bad_engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            SQLModel.metadata.create_all(bad_engine)
            raw = bad_engine.raw_connection()
            raw.execute("DROP TABLE IF EXISTS memory_fts")
            raw.execute("CREATE TABLE memory_fts (memory_id TEXT, content TEXT)")
            raw.commit()

            with (
                patch.object(db_module, "get_session", lambda: Session(bad_engine)),
                patch("lantai.storage.vector_store.ChromaVectorStore"),
            ):
                client = self._client(bad_engine)
                data = client.get("/health/deep").json()

            assert data["checks"]["fts"].startswith("fail"), (
                f"FTS 已坏却报 ok——正是本票要堵的静默链；实得 {data['checks']['fts']}"
            )
            assert data["ok"] is False, "任一子项 fail 则整体不得 ok"
        finally:
            bad_engine.dispose()
            os.unlink(path)
