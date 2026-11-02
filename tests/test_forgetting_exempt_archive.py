"""票 03（P0-4）：豁免记忆的衰减归档误杀 + Hermes 注入保活回写缺失。

不 mock 冒烟（AGENTS.md）：真实 SQLite（StaticPool 内存库）直调 `apply_forgetting`
与 `log_retrieval` / `backfill_used_ids`，衰减公式与归档判定全程真实执行。
时间用**真实 `utcnow` + 把 `last_used_at/created_at` 播种到过去**构造天数差——
不 patch `utcnow`、不 fake helper（票 09 keyset 用例同口径）。
阈值一个都不改：`ARCHIVE_DECAY_THRESHOLD=0.01`、`WORKING_MEMORY_TTL_DAYS=60`
由 `test_settings_defaults_frozen.py` 冻结，本文件把它们作为断言**前提**校验。

两条病灶（复现见工单 Answer）：
1. `forgetting.py` 的 `is_exempt` 只挡 `ttl_expired`，`decay_score < 阈值` 那条路
   照样把高重要度 / 语义 / 程序性记忆归档——等于 TTL 豁免被衰减路开后门。
2. Hermes 注入回执（`/retrieval/backfill`、MCP、shell_hook 注入回执通道）只写
   `RetrievalEvent.used_ids`，从不刷新 `MemoryItem.use_count` / `last_used_at`：
   天天被注入使用的记忆在衰减眼里仍是「65 天没人用过」，直接被归档。
"""

import math
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.core.time import ensure_aware, utcnow
from lantai.memory.forgetting import _lane_strength, apply_forgetting
from lantai.models.tables import MemoryItem, RetrievalEvent
from lantai.observability.retrieval_log import backfill_used_ids, log_retrieval


@pytest.fixture
def engine():
    """真实建表 + 上下文管理的 get_session 复位（票 01 纪律：不留生产全局脏值）。"""
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with patch.object(db_module, "get_session", lambda: Session(e)):
        yield e
    e.dispose()


def _ago(days: float):
    return utcnow() - timedelta(days=days)


def _seed(engine, mid, **kw) -> MemoryItem:
    item = MemoryItem(
        id=mid,
        content=kw.pop("content", f"票03 {mid}"),
        memory_type="text",
        lane=kw.pop("lane", "general"),
        tier=kw.pop("tier", "working"),
        importance=kw.pop("importance", 0.5),
        decay_class=kw.pop("decay_class", "episodic"),
        use_count=kw.pop("use_count", 0),
        helpful_count=kw.pop("helpful_count", 0),
        status=kw.pop("status", "active"),
        decay_score=kw.pop("decay_score", 1.0),
        user_id=kw.pop("user_id", None),
        last_used_at=kw.pop("last_used_at", None),
        created_at=kw.pop("created_at", None),
        **kw,
    )
    with Session(engine) as s:
        s.add(item)
        s.commit()
    return item


def _same_moment(a, b) -> bool:
    """时刻比较走 `ensure_aware`：SQLite 回读是 naive，`utcnow()` 是 aware。"""
    return ensure_aware(a) == ensure_aware(b)


def _status_and_decay(engine, mid):
    with Session(engine) as s:
        m = s.get(MemoryItem, mid)
        return m.status, m.decay_score, m.use_count, m.last_used_at


def _pred_decay(importance, use_count, lane, days) -> float:
    """用真实 `_lane_strength` 预算这条记忆本轮会被算成多少 decay。"""
    return math.exp(-days / _lane_strength(importance, use_count, lane))


class TestExemptionAppliesToDecayArchival:
    """病灶 1：豁免标记必须同时挡住 TTL 路和衰减路（修复目标 1）。"""

    def test_high_importance_not_archived_by_decay_branch(self, engine):
        """importance=0.95 / tier=working / 65 天 / helpful_count=0：正是工单点名的最小复现。

        chat 泳道半衰期极短（S=3+5×0.95=7.75），65 天 decay≈2.3e-4，**远低于** 0.01，
        所以这条只能靠豁免存活；TTL 路它本来就被 `is_exempt` 挡住了。
        """
        days = 65.0
        assert _pred_decay(0.95, 0, "chat", days) < settings.ARCHIVE_DECAY_THRESHOLD, "测试前提失效"
        _seed(
            engine,
            "ex-hi",
            lane="chat",
            importance=0.95,  # 豁免标记（>=0.8）成立
            tier="working",
            helpful_count=0,
            use_count=0,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "ex-hi")
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD, "前提：本例必须已经跌破衰减阈值"
        assert status == "active", f"高重要度记忆被衰减路归档（病灶 1）: decay={decay}"

    @pytest.mark.parametrize(
        "mid,lane,importance,decay_class,days",
        [
            ("ex-sem", "general", 0.50, "semantic", 200),
            ("ex-proc-sem", "chat", 0.10, "semantic", 120),
        ],
    )
    def test_semantic_class_not_archived_by_decay_branch(
        self, engine, mid, lane, importance, decay_class, days
    ):
        """语义记忆（ADR 口径「慢衰减、不因 60 天归档」）不得被衰减路归档。"""
        assert _pred_decay(importance, 0, lane, days) < settings.ARCHIVE_DECAY_THRESHOLD, (
            "测试前提失效"
        )
        _seed(
            engine,
            mid,
            lane=lane,
            importance=importance,
            decay_class=decay_class,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, mid)
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD, "前提：本例必须走衰减路而非 TTL 路"
        assert status == "active", f"语义记忆被衰减路归档（病灶 1）: {mid} decay={decay}"

    def test_decay_semantics_preserved_for_plain_episodic(self, engine):
        """对照组（禁止「永久豁免」）：普通 episodic、低重要度、零使用的陈旧记忆仍须归档。"""
        _seed(
            engine,
            "ctl-old",
            lane="general",
            tier="longterm",
            importance=0.5,
            decay_class="episodic",
            use_count=0,
            last_used_at=_ago(400),
            created_at=_ago(400),
        )
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "ctl-old")
        assert status == "archived", "衰减归档不能因为本票被改成空转"
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD

    def test_working_ttl_branch_still_archives_unused(self, engine):
        """对照：working + 超 60 天 + helpful_count=0 + 无豁免标记 → 仍被 TTL 路归档。"""
        days = settings.WORKING_MEMORY_TTL_DAYS * 1.2
        assert _pred_decay(0.5, 0, "general", days) >= settings.ARCHIVE_DECAY_THRESHOLD, (
            "测试前提失效：本例必须只由 TTL 路归档"
        )
        _seed(
            engine,
            "ctl-work",
            tier="working",
            lane="general",
            importance=0.5,
            helpful_count=0,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        apply_forgetting()
        assert _status_and_decay(engine, "ctl-work")[0] == "archived"

    def test_ttl_exemption_unchanged_for_preference_lane(self, engine):
        """既有 TTL 豁免口径不得被本票收窄：偏好泳道不因 60 天突击归档。"""
        days = settings.WORKING_MEMORY_TTL_DAYS * 1.2
        assert _pred_decay(0.5, 0, "preference", days) >= settings.ARCHIVE_DECAY_THRESHOLD
        _seed(
            engine,
            "ttl-pref",
            lane="preference",
            tier="working",
            importance=0.5,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        apply_forgetting()
        assert _status_and_decay(engine, "ttl-pref")[0] == "active"

    def test_use_count_keepalive_exempts_ttl_but_not_decay(self, engine):
        """`use_count>0` 是**使用保活信号**不是永久豁免：它挡 TTL 突击，衰减仍按 S 走。

        否则用过一次的记忆永生，衰减归档整体空转（工单红线：禁止实现成永久豁免）。
        """
        _seed(
            engine,
            "used-old",
            lane="general",
            tier="longterm",
            importance=0.5,
            use_count=5,
            last_used_at=_ago(200),
            created_at=_ago(200),
        )
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "used-old")
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD, "前提：200 天不用照样跌破阈值"
        assert status == "archived", "「用过一次就永生」不是本票语义"

    def test_eval_pinned_preference_low_importance_still_archives(self, engine):
        """冻结契约不被本票推翻：`chinese_memory_cases` 的 stale 用例明文要求
        `lane=preference, importance=0.1, episodic, 200 天零使用` **必须**归档
        （`test_forgetting_quality.py::test_evaluate_end_to_end` stale_hit_rate==0.0）。
        """
        _seed(
            engine,
            "eval-pref",
            lane="preference",
            tier="long_term",
            importance=0.1,
            decay_class="episodic",
            use_count=0,
            last_used_at=_ago(200),
            created_at=_ago(200),
        )
        apply_forgetting()
        assert _status_and_decay(engine, "eval-pref")[0] == "archived"

    def test_exempt_markers_do_not_freeze_decay_computation(self, engine):
        """豁免只挡「归档」这一动作，衰减值本身仍要如实重算（不掩盖真实衰减）。"""
        days = 200.0
        pred = _pred_decay(0.5, 0, "general", days)
        _seed(
            engine,
            "ex-sem2",
            lane="general",
            importance=0.5,
            decay_class="semantic",
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "ex-sem2")
        assert status == "active"
        assert decay == pytest.approx(pred, rel=1e-3), "豁免不应顺手停算 decay（如实观测）"


def _recall(engine, mid, *, session_id="s-1", principal=None, query="我的编辑器偏好", extra_ids=()):
    """真实注入回执链：log_retrieval（召回埋点）→ backfill_used_ids（注入回执）。"""
    results = [{"memory": {"id": x}, "score": 0.9} for x in (mid, *extra_ids)]
    return log_retrieval(query, results, latency_ms=12, session_id=session_id, principal=principal)


class TestInjectionReceiptKeepsMemoryAlive:
    """病灶 2：Hermes 注入回执必须刷新 `use_count` 与 `last_used_at`（修复目标 2）。"""

    def test_receipt_updates_use_count_and_last_used_at(self, engine):
        stale = _ago(65)
        _seed(engine, "ka-1", lane="chat", importance=0.95, last_used_at=stale, created_at=stale)
        ev = _recall(engine, "ka-1")
        assert ev, "检索事件必须真实落库"
        before = _status_and_decay(engine, "ka-1")
        assert before[2] == 0 and _same_moment(before[3], stale)

        backfill_used_ids(ev, ["ka-1"])

        _status, _decay, use_count, last_used = _status_and_decay(engine, "ka-1")
        assert use_count == 1, f"注入回执没刷 use_count（病灶 2）: {use_count}"
        assert ensure_aware(last_used) > ensure_aware(stale), (
            f"注入回执没刷 last_used_at（病灶 2）: {last_used}"
        )

    def test_hermes_only_usage_survives_forgetting_cycle(self, engine):
        """核心验收：只通过 Hermes 注入/回执使用的记忆，跑完整周期后仍 active。"""
        days = 65.0
        _seed(
            engine,
            "ka-live",
            lane="chat",
            tier="working",
            importance=0.95,
            helpful_count=0,
            use_count=0,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        ev = _recall(engine, "ka-live")
        backfill_used_ids(ev, ["ka-live"])
        apply_forgetting()
        status, decay, use_count, _ = _status_and_decay(engine, "ka-live")
        assert status == "active", f"被注入使用的记忆仍被归档: decay={decay} use_count={use_count}"
        assert decay == pytest.approx(1.0, abs=1e-3), "刚使用过的记忆 decay 应回到 ~1.0"

    def test_receipt_replay_does_not_inflate_use_count(self, engine):
        """整体覆盖语义下重放同一回执不得把 use_count 累加（保活字段是考功输入）。"""
        _seed(engine, "ka-replay", last_used_at=_ago(10), created_at=_ago(10))
        ev = _recall(engine, "ka-replay")
        backfill_used_ids(ev, ["ka-replay"])
        backfill_used_ids(ev, ["ka-replay"])
        backfill_used_ids(ev, ["ka-replay", "ka-replay"])
        assert _status_and_decay(engine, "ka-replay")[2] == 1

    def test_later_receipt_adds_new_ids_only(self, engine):
        """后到的追加回执只给**新增** id 计一次，旧 id 不重复计数。"""
        for mid in ("ka-add-a", "ka-add-b"):
            _seed(engine, mid, last_used_at=_ago(10), created_at=_ago(10))
        ev = _recall(engine, "ka-add-a", extra_ids=("ka-add-b",))
        backfill_used_ids(ev, ["ka-add-a"])
        backfill_used_ids(ev, ["ka-add-a", "ka-add-b"])
        assert _status_and_decay(engine, "ka-add-a")[2] == 1
        assert _status_and_decay(engine, "ka-add-b")[2] == 1

    def test_recall_alone_is_not_usage(self, engine):
        """召回 ≠ 使用：只埋点不回执时不得动保活字段（否则考功/弱标注被污染）。"""
        stale = _ago(65)
        _seed(engine, "ka-recall", last_used_at=stale, created_at=stale)
        _recall(engine, "ka-recall")
        _status, _decay, use_count, last_used = _status_and_decay(engine, "ka-recall")
        assert use_count == 0
        assert _same_moment(last_used, stale)

    def test_receipt_does_not_touch_helpful_count(self, engine):
        """helpful_count 是用户裁决（reflector 反馈），注入回执无权代填。"""
        _seed(engine, "ka-hc", helpful_count=0, last_used_at=_ago(10), created_at=_ago(10))
        ev = _recall(engine, "ka-hc")
        backfill_used_ids(ev, ["ka-hc"])
        with Session(engine) as s:
            assert s.get(MemoryItem, "ka-hc").helpful_count == 0

    def test_receipt_only_bumps_ids_this_event_recalled(self, engine):
        """越界防护：used_ids 里夹带本事件没召回的 id，不给它刷保活字段。"""
        for mid in ("ka-in", "ka-out"):
            _seed(engine, mid, user_id="user-A", last_used_at=_ago(65), created_at=_ago(65))
        ev = _recall(engine, "ka-in")
        backfill_used_ids(ev, ["ka-in", "ka-out"])
        assert _status_and_decay(engine, "ka-in")[2] == 1
        assert _status_and_decay(engine, "ka-out")[2] == 0, "外来 id 被刷了 use_count"

    def test_receipt_skips_other_users_memory(self, engine):
        """归属：保活字段是遗忘/考功的输入，非属主不得代刷（宁 miss 不脏写）。"""
        stale = _ago(65)
        _seed(engine, "ka-own", user_id="user-A", last_used_at=stale, created_at=stale)
        p_a = SimpleNamespace(user_id="user-A", tenant_id=None, is_admin=False, allowed_lanes=None)
        ev = _recall(engine, "ka-own", principal=p_a)
        # 事件属主是 A，但回执由 B 发起（事件归属检查先挡住；这条同时锁住记忆归属检查）
        p_b = SimpleNamespace(user_id="user-B", tenant_id=None, is_admin=False, allowed_lanes=None)
        backfill_used_ids(ev, ["ka-own"], principal=p_b)
        assert _status_and_decay(engine, "ka-own")[2] == 0

    def test_receipt_skips_archived_memory(self, engine):
        """保活不是复活：archived 记忆没有 undo 入口，回执不得悄悄把它改回 active。"""
        _seed(
            engine,
            "ka-arch",
            status="archived",
            last_used_at=_ago(65),
            created_at=_ago(65),
        )
        ev = _recall(engine, "ka-arch")
        backfill_used_ids(ev, ["ka-arch"])
        with Session(engine) as s:
            m = s.get(MemoryItem, "ka-arch")
            assert m.status == "archived"
            assert m.use_count == 0

    def test_empty_used_ids_receipt_writes_nothing(self, engine):
        _seed(engine, "ka-empty", last_used_at=_ago(65), created_at=_ago(65))
        ev = _recall(engine, "ka-empty")
        backfill_used_ids(ev, [])
        _status, _decay, use_count, _lu = _status_and_decay(engine, "ka-empty")
        assert use_count == 0
        with Session(engine) as s:
            assert s.get(RetrievalEvent, ev).used_ids == []


class TestThresholdsAndGlobalsUntouched:
    """本票不调阈值、不在运行期写生产全局（票 01/02 同口径纪律）。"""

    def test_frozen_thresholds_unchanged_by_forgetting_cycle(self, engine):
        before = (
            settings.ARCHIVE_DECAY_THRESHOLD,
            settings.WORKING_MEMORY_TTL_DAYS,
            dict(settings.LANE_DECAY_PROFILES),
        )
        _seed(
            engine, "g-1", lane="chat", importance=0.95, last_used_at=_ago(65), created_at=_ago(65)
        )
        apply_forgetting()
        ev = _recall(engine, "g-1")
        backfill_used_ids(ev, ["g-1"])
        apply_forgetting()
        after = (
            settings.ARCHIVE_DECAY_THRESHOLD,
            settings.WORKING_MEMORY_TTL_DAYS,
            dict(settings.LANE_DECAY_PROFILES),
        )
        assert before == after, "遗忘/注入路径改写了全局阈值"
        assert settings.ARCHIVE_DECAY_THRESHOLD == 0.01
        assert settings.WORKING_MEMORY_TTL_DAYS == 60


class TestConsolidationPrunerRespectsExemption:
    """第三病灶（同一道口的另一扇后门）：沉潜收尾修剪 `prune_decayed_synapses`。

    它是 scheduler 每日挂着的**自动归档任务**（cron 19:30 UTC / 北京 03:30），
    按 `decay_score < CONSOLIDATION_PRUNE_THRESHOLD`（0.05）+ `helpful_count == 0`
    直接置 `archived`，而 archived 没有 undo 入口（docstring 自陈）。

    只修 `forgetting.py` 等于没修，实测（本类第一条用例的链式复现）：

        importance=0.95 / tier=working / lane=chat / 65 天未使用 / helpful_count=0
        apply_forgetting()            -> active（豁免生效）
        prune_decayed_synapses()      -> archived（当晚即被推翻）

    两条路都每日跑（`FORGET_CRON_HOURS=24` + consolidation cron），且修剪阈值 0.05
    比归档阈值 0.01 **更高、更早触发**——不可撤销的那条路反而跑在前面。豁免标记
    必须在**每一条**归档选择器上生效，否则工单验收目标（已豁免记忆不得被误归档）
    只在半个周期内成立。
    """

    def test_pruner_skips_decay_exempt_memories(self, engine):
        from lantai.services.consolidation_service import prune_decayed_synapses

        _seed(
            engine,
            "pr-sem",
            lane="general",
            tier="longterm",
            importance=0.5,
            decay_class="semantic",
            decay_score=0.001,
            helpful_count=0,
        )
        _seed(
            engine,
            "pr-hiimp",
            lane="chat",
            tier="longterm",
            importance=0.9,
            decay_class="episodic",
            decay_score=0.002,
            helpful_count=0,
        )
        _seed(
            engine,
            "pr-proc",
            lane="rule",
            tier="longterm",
            importance=0.5,
            decay_class="procedural",
            decay_score=0.003,
            helpful_count=0,
        )
        with Session(engine) as s:
            pruned = prune_decayed_synapses(session=s)
        assert pruned == 0, f"豁免记忆被沉潜修剪归档: {pruned}"
        for mid in ("pr-sem", "pr-hiimp", "pr-proc"):
            assert _status_and_decay(engine, mid)[0] == "active", mid

    def test_exempt_survives_forgetting_then_consolidation_chain(self, engine):
        """工单主复现的**端到端**版本：遗忘 + 沉潜两条定时任务连跑，豁免必须还活着。

        这条是本单的验收底线本体——单看 `apply_forgetting` 绿不代表记忆真的保住了。
        时间用真实播种（把 `created_at` / `last_used_at` 写到 65 天前），不 patch
        `utcnow`、不 fake helper、不动任何阈值。
        """
        from lantai.services.consolidation_service import (
            prune_decayed_synapses,
            run_consolidation_cycle,
        )

        assert settings.CONSOLIDATION_PRUNE_THRESHOLD > settings.ARCHIVE_DECAY_THRESHOLD

        _seed(
            engine,
            "chain-hiimp",
            user_id="user-A",
            lane="chat",
            tier="working",
            importance=0.95,
            decay_class="episodic",
            helpful_count=0,
            use_count=0,
            created_at=_ago(400),
            last_used_at=_ago(65),
        )
        with patch.object(settings, "CONSOLIDATION_AUDIT_MODE", "off"):
            apply_forgetting()
            assert _status_and_decay(engine, "chain-hiimp")[0] == "active", "遗忘路已误归档"
            # 遗忘把 decay 真实算到接近 0（豁免不等于冻结计算），当晚沉潜必须仍然认豁免
            assert _status_and_decay(engine, "chain-hiimp")[1] < settings.ARCHIVE_DECAY_THRESHOLD
            run_consolidation_cycle()

        assert _status_and_decay(engine, "chain-hiimp")[0] == "active", (
            "豁免记忆在「遗忘保活 + 沉潜修剪」同一天花板上被推翻（票 03 验收目标未达成）"
        )

    def test_pruner_still_archives_plain_fragments(self, engine):
        """对照：非豁免的极低衰减碎片仍照常被修剪（不能把修剪功能修废）。"""
        from lantai.services.consolidation_service import prune_decayed_synapses

        assert settings.CONSOLIDATION_PRUNE_THRESHOLD > settings.ARCHIVE_DECAY_THRESHOLD
        _seed(
            engine,
            "pr-plain",
            lane="general",
            tier="longterm",
            importance=0.5,
            decay_class="episodic",
            decay_score=settings.CONSOLIDATION_PRUNE_THRESHOLD * 0.5,
            helpful_count=0,
        )
        with Session(engine) as s:
            assert prune_decayed_synapses(session=s) == 1
        assert _status_and_decay(engine, "pr-plain")[0] == "archived"


class TestImportanceFloorIsConfigurable:
    """P0-B「阈值可配置」：豁免下限必须是配置项，不能写死在模块常量里。

    判据是**归档行为随配置改变**，不是「设置项存在」——只查字段存在就是假绿：
      · 出厂下限 0.8 下 importance=0.95 的 working 记忆 65 天必须保活；
      · 把下限调到 0.97 后同一条记忆必须被归档，证明 TTL 路与衰减路读的都是
        settings（两处共用 `forgetting._decay_exempt`，见 `consolidation_service`
        复用同一函数的注释）。
    出厂默认值改动由 `test_settings_defaults_frozen.py` 的全字段枚举兜住。
    """

    def _seed_high_importance(self, engine, mid):
        days = 65.0
        assert _pred_decay(0.95, 0, "chat", days) < settings.ARCHIVE_DECAY_THRESHOLD, (
            "测试前提失效：本例必须落在衰减路"
        )
        _seed(
            engine,
            mid,
            lane="chat",
            importance=0.95,
            tier="working",
            helpful_count=0,
            use_count=0,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        return days

    def test_factory_floor_keeps_high_importance_alive(self, engine):
        assert settings.FORGET_IMPORTANCE_EXEMPTION_FLOOR == 0.8, "出厂下限漂移"
        self._seed_high_importance(engine, "cfg-hi")
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "cfg-hi")
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD, "前提：本条必须已跌破阈值"
        assert status == "active", f"出厂下限下高重要度记忆被归档: decay={decay}"

    def test_raising_floor_rearchives_previously_exempt_memory(self, engine, monkeypatch):
        self._seed_high_importance(engine, "cfg-hi2")
        apply_forgetting()
        assert _status_and_decay(engine, "cfg-hi2")[0] == "active", "出厂下限下必须保活"

        monkeypatch.setattr(settings, "FORGET_IMPORTANCE_EXEMPTION_FLOOR", 0.97)
        apply_forgetting()
        status, decay, _, _ = _status_and_decay(engine, "cfg-hi2")
        assert decay < settings.ARCHIVE_DECAY_THRESHOLD, "前提：仍在可判定的衰减区间"
        assert status == "archived", f"阈值不生效（仍被模块常量写死）: decay={decay}"


def test_get_session_binding_restored_after_this_files_cases():
    """末端自检：本文件的用例都在 `patch.object` 上下文里跑，跑完必须回到规范绑定。

    票 01 教训——fixture 把生产全局留在补丁态，后续用例抓到的「原值」本身就是脏的，
    顺序敏感红由此产生。这里不依赖 conftest 兜底复位层，自己证明没留脏值。
    """
    from lantai.storage.db import get_session as pristine

    assert db_module.get_session is pristine, "本文件把 db.get_session 留在了补丁态"
