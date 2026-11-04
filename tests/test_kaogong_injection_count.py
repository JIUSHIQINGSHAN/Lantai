"""考功误降级（巡检 2026-11-03 P0，改法①）：注入次数与显式反馈分账。

病灶：v0.66（`93e73ac7`）让 `backfill_used_ids` 每次宿主回执都给记忆
`use_count += 1`。Hermes 插件每轮注入都发回执，却从不发「有用」反馈
（全仓只有 `reflector.record_feedback` 会加 `helpful_count`）。于是天天被
注入的记忆在考功眼里是「用了 N 次、0 次有用」：

- 考功：`use_count >= 3` 且采纳率 <= 0.2 → 下考，重要度写成 0.1；
- 三省：`use_count >= 5` 且 `helpful_count == 0` → 标「unhelpful_fragment」；
- 降到 0.1 之后失去高重要度豁免，160 天不用就被衰减归档。

改法①：回执只刷 `last_used_at` 并累加独立的 `injection_count`；`use_count`
回到「显式反馈次数」原义，考功 / 三省只看显式反馈；遗忘的保活把两者相加，
v0.66 的注入保活效果不变。老库由迁移 v37 补列，并把 v0.66 已经灌进
`use_count` 的注入次数按反馈账本挪回 `injection_count`。

不 mock 冒烟：真实 SQLite + 真实 `log_retrieval` / `backfill_used_ids` /
`KaogongEngine.run_cycle` / `SanxingEngine.evaluate_health` / `apply_forgetting` /
`apply_migrations`。时间靠把 `last_used_at/created_at` 播种到过去构造。
"""

import sqlite3
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cognition.kaogong import KaogongEngine
from lantai.cognition.sanxing import SanxingEngine
from lantai.core.settings import settings
from lantai.core.time import ensure_aware, utcnow
from lantai.evolution.reflector import record_feedback
from lantai.memory.forgetting import apply_forgetting
from lantai.models.tables import MemoryItem
from lantai.observability.retrieval_log import backfill_used_ids, log_retrieval
from lantai.storage.migrations import TARGET_SCHEMA_VERSION, apply_migrations

USER = SimpleNamespace(user_id="qingshan", tenant_id=None, is_admin=False, allowed_lanes=None)


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with patch.object(db_module, "get_session", lambda: Session(e)):
        yield e
    e.dispose()


def _ago(days: float):
    return utcnow() - timedelta(days=days)


def _seed(engine, mid, **kw) -> None:
    item = MemoryItem(
        id=mid,
        content=kw.pop("content", "我对花生过敏"),
        memory_type="text",
        lane=kw.pop("lane", "preference"),
        tier=kw.pop("tier", "working"),
        importance=kw.pop("importance", 0.95),
        user_id=kw.pop("user_id", "qingshan"),
        last_used_at=kw.pop("last_used_at", _ago(1)),
        created_at=kw.pop("created_at", _ago(1)),
        **kw,
    )
    with Session(engine) as s:
        s.add(item)
        s.commit()


def _get(engine, mid) -> MemoryItem:
    with Session(engine) as s:
        m = s.get(MemoryItem, mid)
        s.expunge(m)
        return m


def _inject(mid: str, times: int) -> None:
    """真实注入回执链：每轮一个检索事件 + 一次回执（Hermes 插件 `_backfill_receipt`）。"""
    for i in range(times):
        ev = log_retrieval(
            f"我能吃花生酱吗？第{i}轮",
            [{"memory": {"id": mid}, "score": 0.9}],
            latency_ms=10,
            session_id=f"s-{i}",
            principal=USER,
        )
        assert ev, "检索事件必须真实落库"
        backfill_used_ids(ev, [mid], principal=USER)


def _push_last_used_back(engine, mid, days: float) -> None:
    with Session(engine) as s:
        m = s.get(MemoryItem, mid)
        m.last_used_at = _ago(days)
        m.created_at = _ago(days + 5)
        s.add(m)
        s.commit()


class TestInjectionDoesNotFeedKaogong:
    def test_receipt_bumps_injection_count_not_use_count(self, engine):
        stale = _ago(30)
        _seed(engine, "m-recv", last_used_at=stale, created_at=stale)
        _inject("m-recv", 1)
        m = _get(engine, "m-recv")
        assert m.use_count == 0, "注入回执不该再算作显式使用（use_count）"
        assert m.injection_count == 1
        assert m.helpful_count == 0
        assert ensure_aware(m.last_used_at) > ensure_aware(stale), "回执仍要刷 last_used_at"

    def test_three_injections_then_kaogong_keeps_importance(self, engine):
        """报告复现：0.95 的过敏记忆注入 3 次 + 跑一次考功，修复前降到 0.1。"""
        _seed(engine, "m-peanut")
        _inject("m-peanut", 3)
        report = KaogongEngine(principal=USER).run_cycle()
        m = _get(engine, "m-peanut")
        assert report["demoted"] == 0, report["details"]
        assert m.importance == pytest.approx(0.95), f"考功把天天在用的记忆降权了: {m.importance}"
        assert (m.use_count, m.injection_count) == (0, 3)

    def test_injected_memory_survives_160_days_after_kaogong(self, engine):
        """报告复现的后半段：降到 0.1 后 160 天被归档；修复后重要度不掉、照样豁免。"""
        _seed(engine, "m-160")
        _inject("m-160", 3)
        KaogongEngine(principal=USER).run_cycle()
        _push_last_used_back(engine, "m-160", 160)
        apply_forgetting()
        assert _get(engine, "m-160").status == "active"

    def test_explicit_feedback_still_drives_kaogong(self, engine):
        """显式反馈语义不变：3 次反馈都说「没用」，考功照样下考。"""
        _seed(engine, "m-bad", importance=0.5, lane="general", content="一条确实没用的记忆")
        for _ in range(3):
            assert record_feedback("m-bad", "q", False, False, 0.0, principal=USER)["ok"]
        report = KaogongEngine(principal=USER).run_cycle()
        m = _get(engine, "m-bad")
        assert report["demoted"] == 1
        assert m.importance == pytest.approx(0.1)
        assert (m.use_count, m.injection_count) == (3, 0)

    def test_injection_plus_helpful_feedback_promotes(self, engine):
        """两本账互不干扰：注入很多次 + 3 次「有用」反馈，采纳率按 3/3 算。"""
        _seed(engine, "m-good", importance=0.6, lane="general")
        _inject("m-good", 7)
        for _ in range(3):
            assert record_feedback("m-good", "q", True, True, 0.0, principal=USER)["ok"]
        m = _get(engine, "m-good")
        assert (m.use_count, m.helpful_count, m.injection_count) == (3, 3, 7)
        res = KaogongEngine.evaluate_item(m)
        assert res.action == "promote_longterm", res.reason


class TestSanxingNoLongerMisflags:
    def test_five_injections_not_flagged_unhelpful(self, engine):
        _seed(engine, "m-sx")
        _inject("m-sx", 6)
        snap = SanxingEngine(principal=USER).evaluate_health()
        flagged = {x["id"] for x in snap.unhelpful_items}
        assert "m-sx" not in flagged, "三省把只被注入、没收到反馈的记忆标成了 unhelpful"

    def test_explicit_unhelpful_still_flagged(self, engine):
        _seed(engine, "m-sx-bad", importance=0.5, lane="general")
        for _ in range(5):
            record_feedback("m-sx-bad", "q", False, False, 0.0, principal=USER)
        snap = SanxingEngine(principal=USER).evaluate_health()
        assert "m-sx-bad" in {x["id"] for x in snap.unhelpful_items}


class TestKeepaliveStillWorks:
    def test_injection_refreshes_decay_for_plain_memory(self, engine):
        """v0.66 保活：65 天没用的普通 chat 记忆，被注入一次后跑遗忘仍 active。"""
        stale = _ago(65)
        _seed(
            engine,
            "m-chat",
            lane="chat",
            importance=0.3,
            content="上周说过想学吉他",
            last_used_at=stale,
            created_at=stale,
        )
        _inject("m-chat", 1)
        apply_forgetting()
        m = _get(engine, "m-chat")
        assert m.status == "active"
        assert m.decay_score == pytest.approx(1.0, abs=1e-3)

    def test_uninjected_twin_is_archived(self, engine):
        """对照：同样的记忆不被注入，65 天后照常归档（保活确实来自注入）。"""
        stale = _ago(65)
        _seed(
            engine,
            "m-chat-twin",
            lane="chat",
            importance=0.3,
            last_used_at=stale,
            created_at=stale,
        )
        apply_forgetting()
        assert _get(engine, "m-chat-twin").status == "archived"

    def test_injection_count_exempts_ttl_like_use_count_did(self, engine):
        """TTL 突击豁免：v0.66 里注入让 use_count>0 而挡住 TTL，改后由 injection_count 承担。"""
        days = settings.WORKING_MEMORY_TTL_DAYS + 5
        kw = dict(
            lane="fact",
            tier="working",
            importance=0.5,
            last_used_at=_ago(days),
            created_at=_ago(days),
        )
        _seed(engine, "m-ttl-inj", injection_count=2, **kw)
        _seed(engine, "m-ttl-cold", **kw)
        apply_forgetting()
        assert _get(engine, "m-ttl-inj").status == "active"
        assert _get(engine, "m-ttl-cold").status == "archived", "对照组应被 TTL 归档"

    def test_injection_count_extends_strength_like_use_count_did(self, engine):
        """衰减强度：注入次数照旧进 log1p 项，与 v0.66 把它算进 use_count 时等价。"""
        from lantai.memory.forgetting import _lane_strength, _usage_signal

        _seed(engine, "m-str", use_count=1, injection_count=4)
        m = _get(engine, "m-str")
        assert _usage_signal(m) == 5
        assert _lane_strength(0.5, _usage_signal(m), "general") == _lane_strength(0.5, 5, "general")


# ---- 迁移 v37：老库补列 + 把 v0.66 灌进 use_count 的注入次数挪回去 ----

_OLD_MEMORYITEM_DDL = """
CREATE TABLE memoryitem (
    id TEXT PRIMARY KEY,
    content TEXT,
    use_count INTEGER DEFAULT 0,
    helpful_count INTEGER DEFAULT 0,
    importance REAL DEFAULT 0.5
)
"""
_OLD_FEEDBACK_DDL = """
CREATE TABLE memoryusagefeedback (
    id TEXT PRIMARY KEY,
    memory_id TEXT,
    helped BOOLEAN DEFAULT 0
)
"""


def _old_v36_db(with_feedback_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_OLD_MEMORYITEM_DDL)
    if with_feedback_table:
        conn.execute(_OLD_FEEDBACK_DDL)
    conn.execute("PRAGMA user_version = 36")
    conn.commit()
    return conn


class TestMigrationV37:
    def test_target_version_bumped(self):
        assert TARGET_SCHEMA_VERSION >= 37

    def test_old_db_gets_column_and_inflated_counts_repaired(self):
        conn = _old_v36_db()
        rows = [
            ("m-inflated", 4, 1),  # v0.66：1 次显式反馈 + 3 次注入回执
            ("m-clean", 2, 1),  # 2 次反馈、没被注入
            ("m-pure-inj", 3, 0),  # 只被注入，从没反馈（报告里的过敏记忆）
            ("m-zero", 0, 0),
        ]
        conn.executemany(
            "INSERT INTO memoryitem (id, content, use_count, helpful_count) VALUES (?, '', ?, ?)",
            rows,
        )
        fb = [("fb1", "m-inflated", 1), ("fb2", "m-clean", 1), ("fb3", "m-clean", 0)]
        conn.executemany("INSERT INTO memoryusagefeedback VALUES (?, ?, ?)", fb)
        conn.commit()

        apply_migrations(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        got = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute(
                "SELECT id, use_count, helpful_count, injection_count FROM memoryitem"
            )
        }
        assert got["m-inflated"] == (1, 1, 3)
        assert got["m-clean"] == (2, 1, 0)
        assert got["m-pure-inj"] == (0, 0, 3)
        assert got["m-zero"] == (0, 0, 0)

    def test_migration_is_idempotent(self):
        conn = _old_v36_db()
        conn.execute("INSERT INTO memoryitem (id, content, use_count) VALUES ('m', '', 3)")
        conn.commit()
        apply_migrations(conn)
        from lantai.storage.migrations import _migrate_v37

        _migrate_v37(conn)  # 重跑不再搬第二次
        assert conn.execute(
            "SELECT use_count, injection_count FROM memoryitem WHERE id='m'"
        ).fetchone() == (0, 3)

    def test_without_feedback_table_only_adds_column(self):
        """没有反馈账本就没法分辨，宁 miss 不脏写：只补列，不动 use_count。"""
        conn = _old_v36_db(with_feedback_table=False)
        conn.execute("INSERT INTO memoryitem (id, content, use_count) VALUES ('m', '', 3)")
        conn.commit()
        apply_migrations(conn)
        assert conn.execute(
            "SELECT use_count, injection_count FROM memoryitem WHERE id='m'"
        ).fetchone() == (3, 0)

    def test_kaogong_after_migration_no_longer_demotes(self, engine):
        """端到端：v0.66 老库里被灌到 use_count=3 的过敏记忆，迁移后跑考功不再降权。"""
        _seed(engine, "m-old", use_count=3)
        raw = engine.raw_connection()
        try:
            raw.execute("PRAGMA user_version = 36")
            apply_migrations(raw)
            raw.commit()
        finally:
            raw.close()
        m = _get(engine, "m-old")
        assert (m.use_count, m.injection_count) == (0, 3)
        KaogongEngine(principal=USER).run_cycle()
        assert _get(engine, "m-old").importance == pytest.approx(0.95)
