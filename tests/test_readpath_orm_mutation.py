"""读路径 ORM 原地突变清除 + resolve_conflict 时区防护（票 .scratch/review-fixes-2026-10/10）。

不 mock 冒烟（真实 MemoryItem / 真实 SQLite 会话 / 真实 FTS+BM25+LIKE 与内嵌
Chroma；仅 embed 为外部网络替身，纪律允许）：
- hybrid._chronos_filter 与 temporal.temporal_view_filter 的降权必须作用于
  **本地副本**：调用后原对象 decay_score 与库中值均未变，而降权结果（副本上的
  有效 decay，打分与 model_dump 的输入）与旧实现一致；
- policies.resolve_conflict 对混来源 naive/aware created_at 正常裁决（修复前
  TypeError）。

红口径：修复前「对象被改 / 库被写回 / TypeError」全红；绿：全绿且语义不变。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.retrieval.hybrid as hybrid_mod
import lantai.storage.db as db_module
from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem
from lantai.storage.fts import init_fts, sync_fts


def _hash_embed(texts):
    import hashlib

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
    # 显式 vf/vt=None（票 `.scratch/v0.64-next-window-2026-11/06` 契约更新）：
    # 裸构造曾被 default_factory 填成 vf=utcnow，I4 收窄后 vf 已知即不再
    # 冒用 unknown_soft 豁免——本文件的 I4 用例意图是「时间完全未知」，
    # 必须显式 None 才能表达（default_factory 污染的又一实证）。
    defaults = dict(
        id=new_id("mem"),
        content="兰台项目部署在本地内网服务器",
        key=None,
        status="active",
        memory_type="general",
        decay_score=1.0,
        created_at=utcnow(),
        valid_from=None,
        valid_to=None,
    )
    defaults.update(kw)
    return MemoryItem(**defaults)


def _engine(*, with_fts: bool = False):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    if with_fts:
        init_fts(e.raw_connection())
    return e


class TestChronosFilterNoMutation:
    """直调 `_chronos_filter`：降权作用于本地副本，ORM 对象不被突变。"""

    def test_expired_item_decay_on_local_copy(self):
        """已过 valid_to：有效降权 0.3×（语义不变），原对象 decay_score 不变。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = _mem(valid_from=utcnow() - timedelta(days=30), valid_to=utcnow() - timedelta(days=1))
        kept = _chronos_filter([m])
        assert len(kept) == 1
        assert kept[0].decay_score == pytest.approx(0.3)
        assert m.decay_score == 1.0

    def test_expired_naive_valid_to_same_semantics(self):
        """naive valid_to（按 UTC 解释）：降权同样落在副本上。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = _mem(
            valid_from=datetime.now() - timedelta(days=30),
            valid_to=datetime.now() - timedelta(days=1),
        )
        kept = _chronos_filter([m])
        assert kept[0].decay_score == pytest.approx(0.3)
        assert m.decay_score == 1.0

    def test_unexpired_item_identity_passthrough(self):
        """未过期条目零改动直通（对象同一性，旧行为本就不突变）。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = _mem(valid_from=utcnow() - timedelta(days=30), valid_to=utcnow() + timedelta(days=1))
        kept = _chronos_filter([m])
        assert kept[0] is m
        assert m.decay_score == 1.0

    def test_not_yet_valid_still_excluded(self):
        """未到 valid_from 仍剔除（过滤语义逐字不变）。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = _mem(valid_from=utcnow() + timedelta(days=1), valid_to=None)
        assert _chronos_filter([m]) == []

    def test_copy_dump_matches_original_except_decay(self):
        """副本与原对象的 dump 除 decay_score 外逐字段一致（输出形态不变）。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = _mem(valid_from=utcnow() - timedelta(days=30), valid_to=utcnow() - timedelta(days=1))
        (copy,) = _chronos_filter([m])
        d_orig = {k: v for k, v in m.model_dump(mode="json").items() if k != "decay_score"}
        d_copy = {k: v for k, v in copy.model_dump(mode="json").items() if k != "decay_score"}
        assert d_orig == d_copy


class TestChronosSessionNoWriteback:
    """带 session 的读调用：对象与库中 decay_score 均未变（票 10 核心场景）。"""

    def test_session_loaded_item_not_mutated_and_db_unchanged(self):
        from lantai.retrieval.hybrid import _chronos_filter

        engine = _engine()
        mid = new_id("mem")
        with Session(engine) as s:
            s.add(
                _mem(
                    id=mid,
                    valid_from=utcnow() - timedelta(days=30),
                    valid_to=utcnow() - timedelta(days=1),
                )
            )
            s.commit()

        with Session(engine) as s:
            row = s.get(MemoryItem, mid)
            assert row.decay_score == 1.0
            kept = _chronos_filter([row])
            assert kept[0].decay_score == pytest.approx(0.3)  # 降权结果仍在（打分输入不变）
            assert row.decay_score == 1.0  # 红：0.3（对象被原地突变）
            assert row not in s.dirty  # session 未被读路径弄脏
            s.commit()  # 检索调用方随后的正常提交
        with Session(engine) as s2:
            assert s2.get(MemoryItem, mid).decay_score == 1.0  # 红：0.3（库被写回）
        engine.dispose()


class TestTemporalViewFilterNoMutation:
    """直调 `temporal_view_filter`：软降权作用于本地副本。"""

    def test_unknown_soft_penalty_on_local_copy(self):
        """I4 unknown_soft：降权 0.8× 生效于副本，原对象不变。"""
        from lantai.retrieval.temporal import temporal_view_filter

        m = _mem()  # event_time=None → unknown_soft
        kept, explain = temporal_view_filter([m], as_of=datetime(2026, 9, 10, tzinfo=UTC))
        assert len(kept) == 1
        assert explain[kept[0].id]["matched_by"] == "unknown_soft"
        assert kept[0].decay_score == pytest.approx(0.8)
        assert m.decay_score == 1.0  # 红：0.8

    def test_fuzzy_precision_penalty_on_local_copy(self):
        """fuzzy 精度事件：乘子降权同样落在副本上。"""
        from lantai.retrieval.temporal import temporal_view_filter

        m = _mem(event_time=datetime(2026, 9, 15, tzinfo=UTC), event_time_precision="fuzzy")
        kept, _ = temporal_view_filter([m], as_of=datetime(2026, 9, 10, tzinfo=UTC))
        assert kept[0].decay_score == pytest.approx(0.8)
        assert m.decay_score == 1.0  # 红：0.8


class TestTemporalSessionNoWriteback:
    def test_session_loaded_item_not_mutated_and_db_unchanged(self):
        from lantai.retrieval.temporal import temporal_view_filter

        engine = _engine()
        mid = new_id("mem")
        with Session(engine) as s:
            s.add(_mem(id=mid))
            s.commit()
            # SQLModel 0.0.42 default_factory 污染补正（票
            # `.scratch/v0.64-next-window-2026-11/08`）：显式 vf=None 在 flush
            # 时被改写为落库时刻，纯 ORM 落不出「时间完全未知」行；I4 收窄后
            # 该行会被误判 missed_soft（无降权）。落库后 SQL 补回 NULL——
            # 与 e2_temporal_cases 同款口径补正，根治走票 08。
            from sqlalchemy import text

            s.execute(text("UPDATE memoryitem SET valid_from = NULL WHERE id = :i"), {"i": mid})
            s.commit()

        with Session(engine) as s:
            row = s.get(MemoryItem, mid)
            kept, _ = temporal_view_filter([row], as_of=datetime(2026, 9, 10, tzinfo=UTC))
            assert kept[0].decay_score == pytest.approx(0.8)
            assert row.decay_score == 1.0  # 红：0.8
            assert row not in s.dirty
            s.commit()
        with Session(engine) as s2:
            assert s2.get(MemoryItem, mid).decay_score == 1.0  # 红：0.8
        engine.dispose()


class TestResolveConflictTimezoneGuard:
    """resolve_conflict：混来源 naive/aware created_at 正常裁决（修复前 TypeError）。"""

    def test_mixed_naive_aware_resolves_by_recency(self):
        from lantai.memory.policies import resolve_conflict

        newer = _mem(id="rc-a", created_at=datetime(2026, 1, 2))  # naive（按 UTC 解释）
        older = _mem(id="rc-b", created_at=datetime(2026, 1, 1, tzinfo=UTC))  # aware
        assert resolve_conflict(newer, older) is newer
        assert resolve_conflict(older, newer) is newer

    def test_same_form_comparisons_unchanged(self):
        """同形态（naive/naive、aware/aware）不回归：新者胜。"""
        from lantai.memory.policies import resolve_conflict

        older = _mem(id="rc-c", created_at=datetime(2026, 1, 1))
        newer = _mem(id="rc-d", created_at=datetime(2026, 1, 2))
        assert resolve_conflict(older, newer) is newer
        a = _mem(id="rc-e", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        b = _mem(id="rc-f", created_at=datetime(2026, 1, 2, tzinfo=UTC))
        assert resolve_conflict(a, b) is b

    def test_priority_still_beats_recency_with_mixed_tz(self):
        """防护只作用于 recency 平局分支：优先级裁决先于新旧。"""
        from lantai.memory.policies import resolve_conflict

        principle_old = _mem(
            id="rc-g", role=CognitiveRole.PRINCIPLE, created_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        observation_new = _mem(
            id="rc-h", role=CognitiveRole.OBSERVATION, created_at=datetime(2026, 1, 2)
        )
        assert resolve_conflict(observation_new, principle_old) is principle_old


@pytest.fixture()
def search_env(tmp_path, monkeypatch):
    """真实内存库（FTS）+ 内嵌 Chroma + 确定性 embed（外部网络替身）。"""
    import lantai.evolution.promoter as promoter_mod
    import lantai.storage.vector_store as vs_module
    from lantai.core.settings import settings

    engine = _engine(with_fts=True)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(hybrid_mod, "embed", _hash_embed)
    monkeypatch.setattr(promoter_mod, "embed", _hash_embed)
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma-ticket10"))
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    yield engine
    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    engine.dispose()


class TestHybridSearchSessionNoWriteback:
    """票 10 首选场景：带 session 的完整检索调用（向量空 → 真实 FTS/BM25/LIKE
    降级路径），过期条目走 _chronos_filter + temporal 过滤；检索后对象与库中
    decay_score 均未变，而输出仍带 0.3× 衰减值（降权语义逐字不变）。"""

    def test_keyword_fallback_search_leaves_db_decay_untouched(self, search_env):
        engine = search_env
        mid = new_id("mem")
        with Session(engine) as s:
            item = _mem(
                id=mid,
                valid_from=utcnow() - timedelta(days=30),
                valid_to=utcnow() - timedelta(days=1),  # 已过期 → chronos 降权路径
                created_at=utcnow() - timedelta(days=30),
            )
            s.add(item)
            sync_fts(s, mid, item.content)
            s.commit()

        sys_principal = Principal(user_id=SYSTEM_VIEWER)
        with Session(engine) as s:
            results = hybrid_mod.hybrid_search(
                "兰台项目部署在本地内网服务器",
                top_k=5,
                use_rerank=False,
                session=s,
                principal=sys_principal,
            )
            assert results, "真实 FTS/BM25/LIKE 降级路径必须召回"
            assert results[0]["memory"]["id"] == mid
            assert results[0]["memory"]["decay_score"] == pytest.approx(0.3)  # 语义不变
            row = s.get(MemoryItem, mid)
            assert row.decay_score == 1.0  # 红：0.3（对象被原地突变）
            s.commit()  # 检索调用方随后的正常提交
        with Session(engine) as s2:
            assert s2.get(MemoryItem, mid).decay_score == 1.0  # 红：0.3（库被写回）
