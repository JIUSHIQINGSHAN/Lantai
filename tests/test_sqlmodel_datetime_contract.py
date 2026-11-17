"""SQLModel UTCDateTime 契约：真实 SQLite 写入、查询、旧库回放与 CAS。

SQLModel 0.0.45 起 datetime 默认映射 UTCDateTime：绑定参数必须 aware，
带偏移输入折算 UTC，SQLite 的历史 naive UTC 文本读回 aware UTC。
本项目既有 UTC 存储无需转换数据；禁止用全局补丁或 NaiveDatetime 回退绕过防护。
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import StatementError
from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


class _DatetimeContractProbe(SQLModel, table=True):
    """最小 datetime 列探针，保留可空字段与显式主键用于真实 CAS。"""

    __tablename__ = "dt_contract_probe"

    id: str = Field(primary_key=True)
    created_at: datetime
    optional_at: datetime | None = None


@pytest.fixture()
def datetime_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    _DatetimeContractProbe.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


_INSTANT = datetime(2026, 8, 2, 14, 41, 19, 234999, tzinfo=UTC)


def _seed(engine, *, stamp=_INSTANT):
    with Session(engine) as session:
        session.add(_DatetimeContractProbe(id="probe-1", created_at=stamp))
        session.commit()


def test_datetime_columns_use_utcdatetime():
    from sqlmodel import UTCDateTime

    from lantai.models.tables import MemoryCandidate, MemoryCheckpoint, MemoryItem, RawDocument

    for model in (
        _DatetimeContractProbe,
        MemoryItem,
        MemoryCandidate,
        MemoryCheckpoint,
        RawDocument,
    ):
        for name, field in model.model_fields.items():
            annotation = field.annotation
            if annotation is datetime or datetime in getattr(annotation, "__args__", ()):
                assert isinstance(model.__table__.columns[name].type, UTCDateTime), (
                    f"{model.__name__}.{name} 必须保留 UTCDateTime 防护"
                )


@pytest.mark.parametrize("offset_minutes", [0, 480, -330])
def test_aware_offsets_persist_and_reload_as_same_utc_instant(datetime_engine, offset_minutes):
    local = _INSTANT.astimezone(timezone(timedelta(minutes=offset_minutes)))
    with Session(datetime_engine) as session:
        probe = _DatetimeContractProbe(id="probe-1", created_at=local)
        session.add(probe)
        session.flush()
        assert probe.created_at is local  # 类型处理在 bind/result 发生，不篡改内存输入
        session.commit()
        session.refresh(probe)
        assert probe.created_at == _INSTANT
        assert probe.created_at.tzinfo is UTC
        assert probe.optional_at is None
    with datetime_engine.connect() as conn:
        raw = conn.execute(text("SELECT created_at FROM dt_contract_probe")).scalar_one()
    assert raw == "2026-08-02 14:41:19.234999"


@pytest.mark.parametrize("operation", ["insert", "update", "query"])
def test_naive_database_parameters_are_rejected(datetime_engine, operation):
    _seed(datetime_engine)
    naive = _INSTANT.replace(tzinfo=None)
    with Session(datetime_engine) as session:
        with pytest.raises(StatementError) as caught:
            if operation == "insert":
                session.add(_DatetimeContractProbe(id="bad", created_at=naive))
                session.flush()
            elif operation == "update":
                session.exec(
                    update(_DatetimeContractProbe)
                    .where(_DatetimeContractProbe.id == "probe-1")
                    .values(optional_at=naive)
                )
            else:
                session.exec(
                    select(_DatetimeContractProbe).where(_DatetimeContractProbe.created_at >= naive)
                ).all()
        assert isinstance(caught.value.orig, ValueError)
        assert "timezone" in str(caught.value.orig).lower()
        session.rollback()
    with Session(datetime_engine) as session:
        rows = session.exec(select(_DatetimeContractProbe)).all()
        assert len(rows) == 1
        assert rows[0].created_at == _INSTANT
        assert rows[0].optional_at is None


def test_aware_query_window_normalizes_offset_and_keeps_half_open_boundary(datetime_engine):
    _seed(datetime_engine)
    end = _INSTANT + timedelta(microseconds=1)
    with Session(datetime_engine) as session:
        session.add(_DatetimeContractProbe(id="at-end", created_at=end))
        session.commit()
        offset = timezone(timedelta(hours=8))
        ids = session.exec(
            select(_DatetimeContractProbe.id).where(
                _DatetimeContractProbe.created_at >= _INSTANT.astimezone(offset),
                _DatetimeContractProbe.created_at < end.astimezone(offset),
            )
        ).all()
        assert ids == ["probe-1"]


def test_schema44_naive_utc_text_reloads_without_schema_or_data_conversion(tmp_path):
    """独立旧格式 SQLite 文件：升级 metadata 不改 DATETIME、schema44 或旧时间文本。"""
    import sqlite3

    path = tmp_path / "legacy-utc.db"
    raw_stamp = "2026-08-02 14:41:19.234999"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE dt_contract_probe (id VARCHAR PRIMARY KEY NOT NULL, "
            "created_at DATETIME NOT NULL, optional_at DATETIME)"
        )
        conn.execute("PRAGMA user_version=44")
        conn.execute("INSERT INTO dt_contract_probe VALUES (?, ?, NULL)", ("legacy", raw_stamp))
        schema_before = conn.execute("PRAGMA table_info(dt_contract_probe)").fetchall()
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        SQLModel.metadata.create_all(engine, tables=[_DatetimeContractProbe.__table__])
        with Session(engine) as session:
            row = session.get(_DatetimeContractProbe, "legacy")
            assert row.created_at == _INSTANT
            assert row.created_at.tzinfo is UTC
            assert row.optional_at is None
            row.optional_at = row.created_at
            session.commit()
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 44
            assert conn.execute("PRAGMA table_info(dt_contract_probe)").fetchall() == schema_before
            assert conn.execute(
                "SELECT created_at, optional_at FROM dt_contract_probe"
            ).fetchone() == (raw_stamp, raw_stamp)
    finally:
        engine.dispose()


def test_legacy_timestamp_cas_matches_once_and_rejects_stale_value(datetime_engine):
    from lantai.storage.cas import atomic_update

    with datetime_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO dt_contract_probe VALUES ('legacy', :stamp, NULL)"),
            {"stamp": "2026-08-02 14:41:19.234999"},
        )
    next_stamp = _INSTANT + timedelta(seconds=1)
    with Session(datetime_engine) as session:
        scanned = session.get(_DatetimeContractProbe, "legacy").created_at
        assert scanned.tzinfo is UTC
        assert (
            atomic_update(
                session,
                _DatetimeContractProbe,
                "legacy",
                {"created_at": next_stamp},
                _DatetimeContractProbe.created_at == scanned,
            )
            == 1
        )
        session.commit()
    with Session(datetime_engine) as session:
        assert (
            atomic_update(
                session,
                _DatetimeContractProbe,
                "legacy",
                {"created_at": _INSTANT},
                _DatetimeContractProbe.created_at == scanned,
            )
            == 0
        )
        session.commit()
        assert session.get(_DatetimeContractProbe, "legacy").created_at == next_stamp
