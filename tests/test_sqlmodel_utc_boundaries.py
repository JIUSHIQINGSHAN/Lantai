"""真实写入入口的 UTC 归一与历史 JSON checkpoint 回放；不 mock 被测计算。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlmodel import select

from lantai.models.tables import MemoryCandidate, MemoryCheckpoint, MemoryItem, RawDocument


@pytest.mark.parametrize("offset_hours", [None, 8, -5])
def test_build_verbatim_item_normalizes_and_persists_timestamps(param_env, offset_hours):
    from lantai.services.memory_service import build_verbatim_item

    sf, _ = param_env
    zone = timezone(timedelta(hours=offset_hours)) if offset_hours is not None else None
    created = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=zone)
    updated = created + timedelta(hours=2)
    expected = created.replace(tzinfo=UTC) if zone is None else created.astimezone(UTC)
    item = build_verbatim_item(
        "历史导入保留事务时间", "fact", created_at=created, updated_at=updated
    )
    for field in ("created_at", "valid_from", "updated_at"):
        assert getattr(item, field).tzinfo is UTC
    assert item.created_at == item.valid_from == expected
    assert item.updated_at == expected + timedelta(hours=2)
    with sf() as session:
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.created_at == expected
        assert item.created_at.tzinfo is UTC
        assert item.valid_from == expected
        assert item.updated_at == expected + timedelta(hours=2)


@pytest.mark.parametrize("offset_hours", [None, 8, -5])
def test_dialogue_import_time_persists_utc_and_keeps_document_dedup(param_env, offset_hours):
    from lantai.ingestion.dialogue import ingest_dialogue

    sf, _ = param_env
    zone = timezone(timedelta(hours=offset_hours)) if offset_hours is not None else None
    created = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=zone)
    expected = created.replace(tzinfo=UTC) if zone is None else created.astimezone(UTC)
    content = "记住：发布之前核对数据库备份时间"
    first = ingest_dialogue(content, created_at=created, source="session_import")
    second = ingest_dialogue(content, created_at=created, source="session_import")
    assert first["ingested"] and second["ingested"]
    with sf() as session:
        docs = session.exec(select(RawDocument)).all()
        candidates = session.exec(select(MemoryCandidate)).all()
        assert len(docs) == 1
        assert len(candidates) == 2
        assert {row.document_id for row in candidates} == {docs[0].id}
        assert docs[0].fetched_at == expected
        assert docs[0].fetched_at.tzinfo is UTC
        for candidate in candidates:
            assert candidate.created_at == expected
            assert candidate.created_at.tzinfo is UTC


def _seed_json_checkpoint(session, stamp):
    """以真实完整 JSON 快照模拟旧版本写出的无偏移 datetime。"""
    row = MemoryItem(id="legacy-json", content="新版内容", version=2)
    snapshot = row.model_dump(mode="json")
    snapshot.update(
        content="旧版内容",
        version=1,
        created_at=stamp,
        updated_at=stamp,
        valid_from=stamp,
        event_time=stamp,
        event_time_precision="second",
        last_used_at=None,
    )
    session.add(row)
    session.add_all(
        [
            MemoryCheckpoint(id="previous-json", memory_id=row.id, version=1, after=snapshot),
            MemoryCheckpoint(
                id="current-json", memory_id=row.id, version=2, after=row.model_dump(mode="json")
            ),
        ]
    )
    session.commit()


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-01-02T03:04:05.123456",
        "2026-01-02T03:04:05.123456Z",
        "2026-01-02T11:04:05.123456+08:00",
    ],
)
def test_rollback_legacy_json_timestamps_restore_same_instant(param_env, stamp):
    from lantai.evolution.promoter import rollback

    sf, _ = param_env
    with sf() as session:
        _seed_json_checkpoint(session, stamp)
    result = rollback("legacy-json")
    assert result["ok"], result
    expected = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    with sf() as session:
        row = session.get(MemoryItem, "legacy-json")
        assert row.content == "旧版内容"
        assert row.version == 3
        for name in ("created_at", "valid_from", "event_time"):
            assert getattr(row, name) == expected
            assert getattr(row, name).tzinfo is UTC
        assert row.updated_at.tzinfo is UTC
        assert row.last_used_at is None
        checkpoints = session.exec(
            select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == row.id)
        ).all()
        assert len(checkpoints) == 3


def test_rollback_corrupt_json_timestamp_preserves_content_and_cas_version(param_env):
    from lantai.evolution.promoter import rollback

    sf, _ = param_env
    with sf() as session:
        _seed_json_checkpoint(session, "invalid-timestamp")
    result = rollback("legacy-json")
    assert not result["ok"]
    assert "corrupt checkpoint snapshot" in result["reason"]
    with sf() as session:
        row = session.get(MemoryItem, "legacy-json")
        assert row.version == 2
        assert row.content == "新版内容"
        assert len(session.exec(select(MemoryCheckpoint)).all()) == 2
