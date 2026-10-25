"""迟到更正闭环（票 10）测试：三轴字段落位、I3 钳制豁免、recency_axis、U4 失效型。

不 mock：真实内存库直调 late_correction + ConflictEngine recency 修正。
"""

from datetime import UTC

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.tables import ConflictEvent, MemoryCheckpoint, MemoryEdge, MemoryItem
from lantai.storage.fts import init_fts


@pytest.fixture()
def correction_env(tmp_path, monkeypatch):
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
    yield engine
    engine.dispose()


def _mem(eid, **kw):
    defaults = dict(id=eid, content=f"内容-{eid}", status="active", event_time_precision="")
    defaults.update(kw)
    return MemoryItem(**defaults)


class TestSupersedeCorrection:
    def test_three_axes_correctly_placed(self, correction_env):
        """U1 语义级：替换型三轴各归其位——superseded_at=今天（事务轴），
        valid_to=锚点（事件轴），supersedes 边 + ConflictEvent 落账。"""
        from datetime import datetime, timezone

        from lantai.cognition.late_correction import apply_supersede_correction

        with db_module.get_session() as s:
            old = _mem(
                "evs-old1",
                content="服务器在 A 机房",
                valid_from=datetime(2026, 8, 1, tzinfo=UTC),
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            new = _mem(
                "evs-new1",
                content="服务器从 9 月起换到 B 机房",
                event_time=datetime(2026, 9, 1, tzinfo=UTC),
                event_time_precision="day",
            )
            s.add_all([old, new])
            s.commit()

            res = apply_supersede_correction(old, new, session=s, reason="机房迁移", actor="tester")
            assert res["ok"] is True
            assert res["clamped"] is False and res["approximated"] is False

            s.refresh(old)
            s.refresh(new)
            # 事务轴：取代判定发生在今天
            assert old.superseded_at is not None
            assert old.superseded_by == "evs-new1"
            assert old.lifecycle_status == "superseded"
            # 事件轴：现实失效点锚定到新值事件时间（SQLite 读回 naive，去 tz 比较）
            assert old.valid_to.replace(tzinfo=None) == datetime(2026, 9, 1)
            assert new.valid_from.replace(tzinfo=None) == datetime(2026, 9, 1)
            # 关系边
            edge = s.exec(
                select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == "evs-new1",
                    MemoryEdge.relation == "supersedes",
                )
            ).first()
            assert edge is not None
            # 落账：override 事件
            cfev = s.exec(
                select(ConflictEvent).where(
                    ConflictEvent.memory_id == "evs-old1",
                    ConflictEvent.kind == "override",
                )
            ).first()
            assert cfev is not None
            assert cfev.detail["correction_type"] == "supersede"
            assert cfev.detail["recency_axis"] == "event"
            assert cfev.detail["clamped"] is False

    def test_i3_clamp_on_backfilled_valid_from(self, correction_env):
        """I3 钳制豁免：锚点早于 old.valid_from（回填形态）→ 钳制 + detail 留原始锚点。"""
        from datetime import datetime, timezone

        from lantai.cognition.late_correction import apply_supersede_correction

        with db_module.get_session() as s:
            # old.valid_from = 回填的 created_at（今天入库）；new 携带更早事件时间
            old = _mem(
                "evs-old2",
                content="用户的主数据库是 PostgreSQL 15",
                valid_from=datetime(2026, 9, 25, tzinfo=UTC),  # 回填 created_at
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            )
            new = _mem(
                "evs-new2",
                content="用户从 2026-01-01 起主数据库是 MySQL 9",
                event_time=datetime(2026, 1, 1, tzinfo=UTC),
                event_time_precision="day",
            )
            s.add_all([old, new])
            s.commit()

            res = apply_supersede_correction(old, new, session=s, reason="迟到更正")
            assert res["ok"] is True
            assert res["clamped"] is True  # 钳制而非拒绝（I3 死锁豁免）

            s.refresh(old)
            assert old.valid_to == old.valid_from  # 钳制到 valid_from
            cfev = s.exec(
                select(ConflictEvent).where(ConflictEvent.memory_id == "evs-old2")
            ).first()
            assert cfev.detail["anchor_raw"] == "2026-01-01T00:00:00+00:00"


class TestCreatedAtFallbackAnchor:
    """票 13：created_at 兜底锚必须归一 UTC（同 event_time/valid_from 分支口径），
    不得让 naive 值流入 anchor 比较面/落库面后静默失败。"""

    def test_created_at_fallback_anchor_is_utc(self, correction_env):
        """红→绿主证据：new 无 event_time、valid_from=None、created_at 为 naive
        （无偏移 ISO 串解析产物，未过 ensure_aware 的直传形态）→ 兜底锚须过
        _ensure_utc。修复前返回裸 naive，与 DB 读回的 aware old.valid_from 比较
        抛 TypeError，被 except 吞成 {"ok": False}——替换型更正静默失败。"""
        from datetime import datetime

        from lantai.cognition.late_correction import correction_anchor

        new = _mem(
            "evs-anchor-naive",
            event_time=None,
            valid_from=None,
            created_at=datetime(2026, 10, 25),  # naive
        )
        anchor = correction_anchor(new)
        assert anchor.tzinfo is not None, "created_at 兜底锚未归一 UTC（票 13）"
        assert anchor.replace(tzinfo=None) == datetime(2026, 10, 25)

    def test_created_at_fallback_correction_succeeds(self, correction_env):
        """票据冒烟口径（真库）：new 无 event_time、valid_from=NULL（内存态，
        ORM 列默认不触发）→ created_at 兜底锚，替换型更正成功、三轴落位。
        （注：经 ORM 落库的 valid_from=NULL 不可达——列默认在 INSERT 时回填
        utcnow，故 DB 读回形态的 NULL valid_from 走不到 created_at 分支。）"""
        from datetime import datetime

        from lantai.cognition.late_correction import apply_supersede_correction

        with db_module.get_session() as s:
            old = _mem(
                "evs-fb-old",
                content="用户的主数据库是 PostgreSQL 15",
                valid_from=datetime(2026, 8, 1, tzinfo=UTC),
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            new = _mem(
                "evs-fb-new",
                content="用户的主数据库是 PostgreSQL 16",
                event_time=None,
                valid_from=None,
                created_at=datetime(2026, 10, 25, tzinfo=UTC),
            )
            assert new.created_at.tzinfo is not None
            s.add(old)
            s.commit()

            res = apply_supersede_correction(old, new, session=s, reason="版本升级")
            assert res["ok"] is True, f"created_at 兜底锚更正失败: {res}"
            assert res["approximated"] is True and res["clamped"] is False
            s.refresh(old)
            # 事件轴：锚定到 created_at（兜底分支），三轴各归其位
            assert old.valid_to.replace(tzinfo=None) == datetime(2026, 10, 25)
            assert old.lifecycle_status == "superseded"
            assert old.superseded_by == "evs-fb-new"


class TestCheckpointSnapshot:
    """票 13：checkpoint before 不得为空 dict——回滚该版本才有 before 可依
    （对照 promoter 各分支均存突变前真实快照的既有口径）。"""

    def test_supersede_checkpoint_before_real_snapshot(self, correction_env):
        from datetime import datetime

        from lantai.cognition.late_correction import apply_supersede_correction

        with db_module.get_session() as s:
            old = _mem(
                "evs-ck-old",
                content="服务器在 A 机房",
                valid_from=datetime(2026, 8, 1, tzinfo=UTC),
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            new = _mem(
                "evs-ck-new",
                content="服务器从 9 月起换到 B 机房",
                event_time=datetime(2026, 9, 1, tzinfo=UTC),
                event_time_precision="day",
            )
            s.add_all([old, new])
            s.commit()

            res = apply_supersede_correction(old, new, session=s, reason="机房迁移")
            assert res["ok"] is True
            ckpt = s.exec(
                select(MemoryCheckpoint).where(
                    MemoryCheckpoint.memory_id == "evs-ck-old",
                    MemoryCheckpoint.trigger == "late_correction",
                )
            ).first()
            assert ckpt is not None
            # before = 突变前真实快照（JSON 形态，promoter 同款）
            assert ckpt.before, "checkpoint before 为空快照（票 13）"
            assert ckpt.before["lifecycle_status"] == "active"
            assert ckpt.before["superseded_by"] is None
            assert ckpt.before["valid_to"] is None
            assert ckpt.before["content"] == "服务器在 A 机房"
            # after 为突变后状态，before/after 成对可回滚
            assert ckpt.after["lifecycle_status"] == "superseded"
            assert ckpt.after["superseded_by"] == "evs-ck-new"

    def test_expire_checkpoint_before_real_snapshot(self, correction_env):
        from datetime import datetime

        from lantai.cognition.late_correction import apply_expire_correction

        with db_module.get_session() as s:
            old = _mem(
                "evs-ck-exp",
                content="签证 10 月 31 日到期",
                valid_from=datetime(2026, 10, 1, tzinfo=UTC),
            )
            s.add(old)
            s.commit()

            res = apply_expire_correction(
                old, valid_to=datetime(2026, 10, 31, tzinfo=UTC), reason="签证到期", session=s
            )
            assert res["ok"] is True
            ckpt = s.exec(
                select(MemoryCheckpoint).where(
                    MemoryCheckpoint.memory_id == "evs-ck-exp",
                    MemoryCheckpoint.trigger == "late_correction",
                )
            ).first()
            assert ckpt is not None
            assert ckpt.before, "失效型 checkpoint before 为空快照（票 13）"
            assert ckpt.before["valid_to"] is None
            assert ckpt.before["content"] == "签证 10 月 31 日到期"
            assert ckpt.after["valid_to"] is not None


class TestRecencyAxis:
    def test_recency_prefers_event_time(self, correction_env):
        """直断 recency：event_time 有 → axis=event；无 → axis=created。"""
        from datetime import datetime, timezone

        from lantai.cognition.conflicts import ConflictEngine
        from lantai.models.tables import CognitiveRole

        engine_resolver = ConflictEngine()
        new_with_et = _mem(
            "evs-ra",
            content="用户的主数据库是 MySQL 9",
            created_at=datetime(2026, 9, 25, tzinfo=UTC),
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            event_time_precision="day",
        )
        rec, axis = (
            engine_resolver._score_with_components(new_with_et, None) if False else (None, None)
        )
        # _score_with_components 返回 (score, components)；直接断言 axis
        score, comps = ConflictEngine()._score_with_components(new_with_et, None)
        assert comps["recency_axis"] == "event"

        new_no_et = _mem(
            "evs-rb",
            content="用户的主数据库是 MySQL 9",
            created_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        score2, comps2 = ConflictEngine()._score_with_components(new_no_et, None)
        assert comps2["recency_axis"] == "created"


class TestExpireCorrection:
    def test_expire_writes_valid_to_only(self, correction_env):
        """失效型：仅回写 valid_to（无 supersedes 边、无新值）；I3 违例拒写。"""
        from datetime import datetime, timezone

        from lantai.cognition.late_correction import apply_expire_correction

        with db_module.get_session() as s:
            old = _mem(
                "evs-exp",
                content="签证 9 月 30 日到期",
                valid_from=datetime(2026, 9, 1, tzinfo=UTC),
            )
            s.add(old)
            s.commit()

            res = apply_expire_correction(
                old,
                valid_to=datetime(2026, 9, 30, tzinfo=UTC),
                reason="签证到期",
                session=s,
            )
            assert res["ok"] is True
            s.refresh(old)
            assert old.valid_to.replace(tzinfo=None) == datetime(2026, 9, 30)
            assert old.superseded_by is None  # 无新值无 supersedes
            edges = s.exec(select(MemoryEdge).where(MemoryEdge.target_memory_id == "evs-exp")).all()
            assert not [e for e in edges if e.relation == "supersedes"]

            # I3 违例：valid_to 早于 valid_from → 拒写
            res_bad = apply_expire_correction(
                old,
                valid_to=datetime(2026, 8, 31, tzinfo=UTC),
                reason="矛盾",
                session=s,
            )
            assert res_bad["ok"] is False and "I3" in res_bad["error"]
