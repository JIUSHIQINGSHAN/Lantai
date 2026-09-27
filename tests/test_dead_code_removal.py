"""死代码删除后的「不再存在」断言：票 .scratch/dead-code-removal/01

本文件随删除动作同一个 commit 从「删除前行为证明」改写而来。原证明用
不 mock 直调 `ConflictEngine._score` 与 `temporal._validity_hit`，验证
六维加权与四种有效期边界与 docstring 一致、且与真正被调用的
`_score_with_components` / `asof_matches` 内联段等价——删除不丢行为。
目的已达，现固化为「这两个私有函数不得复活」。

它们不是性能问题，是**重复真源**：六维评分公式曾有两份独立拷贝，
谁照抄第三份就会再次出现「改一处漏一处」而全仓测试全绿的危险面。

删除前实测踩到的三个坑（都记在票据里，免得下次重蹈）：
1. `valid_from` 列是 `default_factory=utcnow`（tables.py:149）——
   **永远不是 NULL**，写「两端全 NULL」的用例必须显式传 None；
2. `components` 各轴已 `round(...,3)`，从它复算总分不可能精确相等
   （实测 0.56995 vs 0.57000），容差须给到 1e-3；
3. 判「函数有无调用点」不能看名字子串——`def _validity_hit(item, ...`
   这行本身就含 `_validity_hit(item`，须用 `(?<!def )` 负回顾。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cognition.conflicts import ConflictEngine
from lantai.models.tables import MemoryItem


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def _mem(eid, **kw):
    defaults = dict(id=eid, content=f"内容-{eid}", status="active", event_time_precision="")
    defaults.update(kw)
    return MemoryItem(**defaults)


class TestDeadScoreIsGone:
    """`ConflictEngine._score` 已删：六维评分只剩 `_score_with_components` 一个真源。"""

    def test_no_bare_score_method(self):
        assert not hasattr(ConflictEngine, "_score")

    def test_score_with_components_is_the_only_truth(self, env):
        """唯一真源仍工作，且公式仍是那六维加权（复算锁住）。"""
        engine = ConflictEngine()
        with Session(env) as s:
            s.add(
                _mem(
                    "dc-1",
                    content="服务器在 A 机房",
                    confidence=0.9,
                    created_at=datetime(2026, 9, 1, tzinfo=UTC),
                    metadata={"domain": "ops"},
                )
            )
            s.commit()
            item = s.get(MemoryItem, "dc-1")

        score, comps = engine._score_with_components(item, None)
        assert set(comps) >= {
            "evidence_strength",
            "confidence",
            "provenance_quality",
            "recency",
            "independence",
            "contextual_fit",
        }
        recomputed = (
            engine._W_EVIDENCE_STRENGTH * comps["evidence_strength"]
            + engine._W_CONFIDENCE * comps["confidence"]
            + engine._W_PROVENANCE * comps["provenance_quality"]
            + engine._W_RECENCY * comps["recency"]
            + engine._W_INDEPENDENCE * comps["independence"]
            + engine._W_CONTEXTUAL * comps["contextual_fit"]
        )
        # 容差 1e-3：components 各轴已 round(...,3)（见模块 docstring 坑 2）
        assert recomputed == pytest.approx(score, abs=1e-3)

    def test_no_second_copy_in_source(self):
        """源码里不得再出现第二份六维加权——这是本票的真正目的。

        判据是「权重被乘用的位置数」而非「名字出现次数」：定义行
        （`_W_EVIDENCE_STRENGTH = 0.30`）本身也含该名字，数字符串会误判
        （本轮实测 2 == 1 失败，就是忘了定义行）。
        """
        import inspect

        src = inspect.getsource(ConflictEngine)
        assert src.count("_W_EVIDENCE_STRENGTH *") == 1  # 只有一处加权
        assert "_score_with_components" in src  # 真源还在

    def test_defaults_when_fields_missing(self, env):
        """无 confidence / 无 scope 时走 0.5 兜底——真源的这个行为不变。"""
        engine = ConflictEngine()
        with Session(env) as s:
            s.add(_mem("dc-2", content="裸记忆"))
            s.commit()
            item = s.get(MemoryItem, "dc-2")

        _, comps = engine._score_with_components(item, None)
        assert comps["confidence"] == 0.5
        assert comps["provenance_quality"] == 0.5


class TestDeadValidityHitIsGone:
    """`temporal._validity_hit` 已删：有效期判定只剩 `asof_matches` 内联那一处。"""

    def test_no_validity_hit_function(self):
        from lantai.retrieval import temporal

        assert not hasattr(temporal, "_validity_hit")

    def test_asof_matches_still_decides_validity(self):
        """内联段仍在工作——删掉的只是没人调的那份副本。"""
        from lantai.retrieval.temporal import asof_matches

        t = datetime(2026, 9, 10, tzinfo=UTC)
        inside = _mem("vh-1", valid_from=t - timedelta(days=5), valid_to=t + timedelta(days=5))
        ok, by = asof_matches(inside, t)
        assert ok and by == "validity"

    def test_expired_and_future_fall_through_to_i4_not_excluded(self):
        """**删除前实测发现的语义分歧，此处固化（两条一起）**：

        已删的 `_validity_hit` 对「valid_to <= as_of」（已失效）与
        「valid_from > as_of」（尚未生效）都返回 **False**；而真正被调用的
        `asof_matches` 对同样输入走的是 **I4 unknown_soft → True**（放行、
        仅乘 fuzzy_penalty 降权）。那份死代码不只是没人调，它与活路径
        **结论相反**——若当初有人顺手接上，会得到「过期/未来主张被硬剔除」，
        与 spec §2.5 I4「任何模式都不因时间条件硬排除」直接冲突。

        `strict=True` 也不剔除（I4 是硬豁免）。这是 settings.py:192
        `TEMPORAL_CURRENT_STRICT` 注释所记的**有意延后决定**：
        「当前态收紧：已过 valid_to 退出（默认关，须独立票验证后再开）」。
        故本票只删死代码、**不改产品行为**；真要让过期主张退出召回，
        走那张独立票（见 .scratch/expiry-exclusion/）。

        现在函数已删，这条断言锁住 `asof_matches` 的现行行为不被顺手改坏。
        """
        from lantai.retrieval.temporal import asof_matches

        t = datetime(2026, 9, 10, tzinfo=UTC)
        cases = {
            "expired": _mem("vh-3", valid_to=t),
            "future": _mem("vh-2", valid_from=t + timedelta(days=1)),
        }
        for name, item in cases.items():
            for strict in (False, True):
                ok, by = asof_matches(item, t, strict=strict)
                assert ok is True and by == "unknown_soft", (name, strict, ok, by)

    def test_default_factory_makes_valid_from_never_null(self):
        """固化 tables.py:149 的 default_factory 语义（删除前实测踩到的坑 1）。"""
        bare = _mem("vh-7")
        assert bare.valid_from is not None  # utcnow 自动填，故列无稳定 NULL
        assert bare.valid_to is None

    def test_event_interval_hit_survives(self):
        """同文件的 `_event_interval_hit` 有真实调用点，不得被顺手删掉。"""
        from lantai.retrieval import temporal

        assert hasattr(temporal, "_event_interval_hit")
