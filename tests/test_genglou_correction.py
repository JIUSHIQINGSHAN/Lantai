"""迟到更正闭环（票 10）测试：三轴字段落位、I3 钳制豁免、recency_axis、U4 失效型。

不 mock：真实内存库直调 late_correction + ConflictEngine recency 修正。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.models.tables import ConflictEvent, MemoryEdge, MemoryItem
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
                valid_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            new = _mem(
                "evs-new1",
                content="服务器从 9 月起换到 B 机房",
                event_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                event_time_precision="day",
            )
            s.add_all([old, new])
            s.commit()

            res = apply_supersede_correction(
                old, new, session=s, reason="机房迁移", actor="tester"
            )
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
                valid_from=datetime(2026, 9, 25, tzinfo=timezone.utc),  # 回填 created_at
                created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
            )
            new = _mem(
                "evs-new2",
                content="用户从 2026-01-01 起主数据库是 MySQL 9",
                event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
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
            created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
            event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            event_time_precision="day",
        )
        rec, axis = engine_resolver._score_with_components(new_with_et, None) if False else (None, None)
        # _score_with_components 返回 (score, components)；直接断言 axis
        score, comps = ConflictEngine()._score_with_components(new_with_et, None)
        assert comps["recency_axis"] == "event"

        new_no_et = _mem(
            "evs-rb",
            content="用户的主数据库是 MySQL 9",
            created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
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
                valid_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
            s.add(old)
            s.commit()

            res = apply_expire_correction(
                old,
                valid_to=datetime(2026, 9, 30, tzinfo=timezone.utc),
                reason="签证到期",
                session=s,
            )
            assert res["ok"] is True
            s.refresh(old)
            assert old.valid_to.replace(tzinfo=None) == datetime(2026, 9, 30)
            assert old.superseded_by is None  # 无新值无 supersedes
            edges = s.exec(
                select(MemoryEdge).where(MemoryEdge.target_memory_id == "evs-exp")
            ).all()
            assert not [e for e in edges if e.relation == "supersedes"]

            # I3 违例：valid_to 早于 valid_from → 拒写
            res_bad = apply_expire_correction(
                old,
                valid_to=datetime(2026, 8, 31, tzinfo=timezone.utc),
                reason="矛盾",
                session=s,
            )
            assert res_bad["ok"] is False and "I3" in res_bad["error"]
