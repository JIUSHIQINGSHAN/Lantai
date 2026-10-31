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
        """**契约更新（票 `.scratch/v0.64-next-window-2026-11/06`，2026-10-31）**：

        原断言（vf/vt 已知且不在窗口 → I4 unknown_soft 放行）锁的是
        chronos 越权剔除掩盖下的掩体行为：有效期已知 =「确定不在」，
        不是 I4 保护的「时间锚不确定」（ADR-0048 边界小节 I4 原文的
        「时间锚」指 event_time；vf/vt 是主张有效期，不是事件锚）。
        票 06 修复 chronos 越权后，asof_matches 的 I4 收窄为
        「vf/vt/event_time 全 NULL（时间信息完全未知）才豁免」；
        vf/vt 已知且 as-of 落在有效期之外 → strict 剔除 / 非 strict
        missed_soft 降权（宁多见不错删在非 strict 下不受损）。

        与 `.scratch/expiry-exclusion/01`（ready-for-human）作用域不相交：
        该票审议的是**当前态**（TEMPORAL_CURRENT_STRICT，无 as_of 参数）
        过期记忆退出召回；本函数 `asof_matches` 只服务 as-of 显式历史视图。
        """
        from lantai.retrieval.temporal import asof_matches

        t = datetime(2026, 9, 10, tzinfo=UTC)
        # 时间信息完全未知（vf/vt/event_time 全 NULL）→ I4 豁免不变
        # （显式 None：default_factory 会让构造时 vf=utcnow，那就不是「未知」了）
        blank = _mem("vh-1", valid_from=None, valid_to=None)
        for strict in (False, True):
            ok, by = asof_matches(blank, t, strict=strict)
            assert ok is True and by == "unknown_soft", (strict, ok, by)
        # 有效期已知且 as-of 在其外 → 不再冒用 I4：strict 剔除 / 非 strict 降权保留
        cases = {
            "expired": _mem("vh-3", valid_from=t - timedelta(days=30), valid_to=t),
            "future": _mem("vh-2", valid_from=t + timedelta(days=1)),
        }
        for name, item in cases.items():
            ok, by = asof_matches(item, t, strict=False)
            assert ok is True and by == "missed_soft", (name, ok, by)
            ok, by = asof_matches(item, t, strict=True)
            assert ok is False and by == "", (name, ok, by)

    def test_default_factory_makes_valid_from_never_null(self):
        """**契约更新（票 `.scratch/v0.64-next-window-2026-11/08`，2026-10-31）**：

        原断言锁 default_factory「裸构造 vf 恒 utcnow」——那是被根治的污染
        行为：default_factory 会把显式 None 改写为 utcnow，「开端开放」无法
        表达。列默认改 `default=None` 后（写入点显式 `valid_from=utcnow()`
        声明「现在生效」），裸构造 = vf None 保真；产品写入点的「现在生效」
        语义由 `test_valid_from_open_interval.py` 的服务层冒烟锁定。
        保留原测试名（删除前实测踩到的坑 1 的历史出处）。
        """
        bare = _mem("vh-7")
        assert bare.valid_from is None  # 列默认 None：显式 None/缺省都不再被改写
        assert bare.valid_to is None

    def test_event_interval_hit_survives(self):
        """同文件的 `_event_interval_hit` 有真实调用点，不得被顺手删掉。"""
        from lantai.retrieval import temporal

        assert hasattr(temporal, "_event_interval_hit")
