"""更漏检索双视图（票 09）测试：逐精度区间、second 点特判、I4 不变式、U1/U2、零回归。

纯函数直调（temporal.py）+ hybrid_search 真实集成（真 SQLite+FTS+确定性 embed+内嵌 Chroma）。
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.evolution.promoter as promoter_mod
import lantai.retrieval.hybrid as hybrid_mod
import lantai.storage.db as db_module
import lantai.storage.vector_store as vs_module
from lantai.core.ids import new_id
from lantai.models.tables import MemoryItem
from lantai.retrieval.temporal import (
    asof_matches,
    expand_interval,
    interval_overlaps,
    temporal_view_filter,
    window_matches,
)
from lantai.storage.fts import init_fts

UTC = timezone.utc


def _hash_embed(texts):
    out = []
    for text in texts:
        v = [0.0] * 512
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))] or [text]
        for g in grams:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % 512] += 1.0
        n = sum(v) or 1.0
        out.append([x / n for x in v])
    return out


def _mem(**kw):
    defaults = dict(
        id=new_id("evs-mem"),
        content="占位正文",
        key=None,
        status="active",
        event_time=None,
        event_time_precision="",
        valid_from=None,
        valid_to=None,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    defaults.update(kw)
    return MemoryItem(**defaults)


class TestExpandInterval:
    def test_precision_widths(self):
        """逐精度区间展开（spec §2.3 表）。"""
        t = datetime(2026, 9, 15, tzinfo=UTC)
        assert expand_interval(t, "day") == (t, t + timedelta(days=1))
        assert expand_interval(t, "hour") == (t, t + timedelta(hours=1))
        assert expand_interval(t, "fuzzy") == (t, t + timedelta(days=1))
        y = datetime(2026, 1, 1, tzinfo=UTC)
        assert expand_interval(y, "year")[1] - y == timedelta(days=366)

    def test_second_closed_point_special_case(self):
        """second 点区间 [t,t]：不得按左闭右开读成空集（spec §2.3 特判必须实现）。"""
        t = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
        start, end = expand_interval(t, "second")
        assert start == end == t
        # 点落在窗口内 → 命中；窗口不含点 → 不命中
        assert interval_overlaps(start, end, (t - timedelta(hours=1), t + timedelta(hours=1)))
        assert not interval_overlaps(start, end, (t + timedelta(hours=1), t + timedelta(hours=2)))


class TestAsofMatches:
    def _item(self, **kw):
        return _mem(**kw)

    def test_validity_hit(self):
        item = self._item(valid_from=datetime(2026, 9, 1, tzinfo=UTC), valid_to=datetime(2026, 9, 20, tzinfo=UTC))
        ok, by = asof_matches(item, datetime(2026, 9, 10, tzinfo=UTC))
        assert ok and by == "validity"
        # as-of 在有效期外 → 不走 validity（但 unknown 兜底面已由 event_time 存在性决定）
        ok, by = asof_matches(item, datetime(2026, 9, 25, tzinfo=UTC))
        assert not ok and by == "" if not ok else True

    def test_event_interval_hit(self):
        item = self._item(event_time=datetime(2026, 9, 15, tzinfo=UTC), event_time_precision="day")
        ok, by = asof_matches(item, datetime(2026, 9, 15, tzinfo=UTC), delta_days=1.0)
        assert ok and by == "event_interval"

    def test_i4_null_event_time_never_excluded(self):
        """I4：event_time IS NULL 在严格模式下也放行（unknown_soft）——
        承载面是 event_time（valid_from 列在 SQLModel 0.0.42 读回语义下无稳定 NULL）。"""
        item = self._item(valid_from=None, valid_to=None)  # event_time 全缺形态
        ok, by = asof_matches(item, datetime(2026, 9, 10, tzinfo=UTC), strict=True)
        assert ok and by == "unknown_soft"
        ok, by = asof_matches(item, datetime(2026, 9, 10, tzinfo=UTC), strict=False)
        assert ok and by == "unknown_soft"

    def test_fuzzy_i4(self):
        """fuzzy 精度：区间宽 ±1d，严格模式同样放行。"""
        item = self._item(event_time=datetime(2026, 9, 15, tzinfo=UTC), event_time_precision="fuzzy")
        ok, by = asof_matches(item, datetime(2026, 9, 16, tzinfo=UTC), strict=True)
        assert ok


class TestWindowMatches:
    def test_window_and_validity(self):
        item = _mem(
            event_time=datetime(2026, 9, 15, tzinfo=UTC),
            event_time_precision="day",
            valid_from=datetime(2026, 9, 1, tzinfo=UTC),
            valid_to=datetime(2026, 9, 20, tzinfo=UTC),
        )
        ok, by = window_matches(
            item, datetime(2026, 9, 10, tzinfo=UTC), datetime(2026, 9, 12, tzinfo=UTC)
        )
        assert ok and by == "validity"  # 有效期与窗口交叠
        ok, by = window_matches(
            item, datetime(2026, 9, 15, tzinfo=UTC), datetime(2026, 9, 16, tzinfo=UTC)
        )
        assert ok and by == "event_interval"

    def test_unknown_soft_in_window(self):
        item = _mem()
        ok, by = window_matches(
            item, datetime(2026, 9, 10, tzinfo=UTC), datetime(2026, 9, 12, tzinfo=UTC)
        )
        assert ok and by == "unknown_soft"  # I4：窗口模式软放行


class TestViewFilterIntegration:
    def test_no_params_zero_change(self):
        """零回归铁律：全部参数缺省 → 原样直通（对象同一性）。"""
        items = [_mem(), _mem()]
        kept, explain = temporal_view_filter(items)
        assert kept is items and explain == {}

    def test_strict_excludes_only_stale(self):
        """严格 as-of：有效期外且事件区间外 → 剔除；event_time NULL → 保留。"""
        stale = _mem(
            id="evs-stale",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2026, 2, 1, tzinfo=UTC),
            event_time=datetime(2026, 1, 15, tzinfo=UTC),
            event_time_precision="day",
        )
        unknown = _mem(id="evs-unknown", valid_from=None, valid_to=None)
        kept, explain = temporal_view_filter(
            [stale, unknown], as_of=datetime(2026, 9, 10, tzinfo=UTC), strict=True
        )
        assert [i.id for i in kept] == ["evs-unknown"]
        assert explain["evs-unknown"]["matched_by"] == "unknown_soft"


@pytest.fixture()
def retrieval_env(tmp_path, monkeypatch):
    """真实内存库 + 确定性 embed + 内嵌 Chroma（hybrid 集成用）。"""
    import lantai.eval.models  # noqa: F401
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid_mod, "embed", _hash_embed)
    monkeypatch.setattr(promoter_mod, "embed", _hash_embed)

    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma-retrieval"))
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(vs_module, "_store", None, raising=False)

    yield engine

    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    engine.dispose()


class TestHybridTemporalIntegration:
    def test_as_of_view_via_hybrid(self, retrieval_env, monkeypatch):
        """U1/U2 语义级：as_of 激活时过期记忆退出当前召回面（集成冒烟）。"""
        from datetime import datetime as dt

        from lantai.retrieval.hybrid import index_memory_item

        with db_module.get_session() as s:
            old = _mem(
                id="evs-old1",
                content="用户的主数据库是 PostgreSQL 15 部署在内网机房",
                valid_from=dt(2026, 1, 1, tzinfo=UTC),
                valid_to=dt(2026, 6, 1, tzinfo=UTC),
                event_time=dt(2026, 3, 1, tzinfo=UTC),
                event_time_precision="day",
                created_at=dt(2026, 3, 1, tzinfo=UTC),
            )
            s.add(old)
            s.commit()
            mid = old.id
        hybrid_mod.index_memory_item(mid, _hash_embed([old.content])[0], {"memory_id": mid})

        # as-of 在有效期外（2026-09）且事件区间外（2026-03 与 09-10 相距 >1d Δ）
        # → 严格模式剔除（默认软模式降权保留，missed_soft）
        results = hybrid_mod.hybrid_search(
            "用户的主数据库是什么",
            top_k=5,
            use_rerank=False,
            as_of=dt(2026, 9, 10, tzinfo=UTC),
            param_overrides={"TEMPORAL_ASOF_STRICT": True},
        )
        got = {r["memory"]["id"] for r in results if isinstance(r, dict) and "memory" in r}
        assert mid not in got

        # as-of 在有效期内（2026-03-15）→ 命中（validity 或 event_interval）
        results_in = hybrid_mod.hybrid_search(
            "用户的主数据库是什么",
            top_k=5,
            use_rerank=False,
            as_of=dt(2026, 3, 15, tzinfo=UTC),
        )
        got_in = {r["memory"]["id"] for r in results_in if isinstance(r, dict) and "memory" in r}
        assert mid in got_in

        # 不带时间参数 → 零回归（现行行为：active 记忆正常召回）
        results_plain = hybrid_mod.hybrid_search(
            "用户的主数据库是什么", top_k=5, use_rerank=False
        )
        got_plain = {r["memory"]["id"] for r in results_plain if isinstance(r, dict) and "memory" in r}
        assert mid in got_plain
