"""终端 Session API 迁移：真实临时 SQLite、FTS、归属与事务。

路由/helper/ACL/级联不打桩。仅重定向数据库与向量外部存储，embedding 使用
conftest 的外部网络替身。回滚用真实 SQLite trigger 拒写，覆盖已发生的 FTS、
事实、边变更，避免 mock 内部调用而跳过迁移后的 SQL 执行。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api import routes_terminal as terminal
from lantai.core.auth import Principal
from lantai.models.tables import FactAtomic, FactEntity, MemoryAuditEvent, MemoryEdge, MemoryItem
from lantai.storage.fts import init_fts, search_fts, sync_fts

_OWNER = Principal(user_id="owner", tenant_id="tenant", allowed_lanes=["general"])


class _VectorLedger:
    """只替外部存储，保留真实 sync_vector_* / hybrid 调用。"""

    def __init__(self):
        self.added = []
        self.deleted = []

    def add(self, *, ids, embeddings, metadatas):
        self.added.extend(ids)

    def delete(self, ids):
        self.deleted.extend(ids)


@pytest.fixture()
def terminal_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'terminal.sqlite'}")
    SQLModel.metadata.create_all(engine)
    with engine.connect() as connection:
        assert init_fts(connection.connection.driver_connection)
    monkeypatch.setattr(db_module, "engine", engine)
    ledger = _VectorLedger()
    monkeypatch.setattr("lantai.storage.vector_store.get_vector_store", lambda: ledger)
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: ledger)
    try:
        yield engine, ledger
    finally:
        engine.dispose()


def _seed(engine, **overrides):
    with Session(engine) as session:
        for memory_id in ("source", "target", "other"):
            fields = {
                "id": memory_id,
                "content": f"原始正文-{memory_id}",
                "memory_type": "semantic",
                "user_id": "owner",
                "tenant_id": "tenant",
                "lane": "general",
                "importance": 0.9 if memory_id == "source" else 0.7,
            }
            if memory_id == "source":
                fields.update(overrides)
            session.add(MemoryItem(**fields))
            sync_fts(session, memory_id, fields["content"])
        session.add_all(
            [
                MemoryEdge(
                    id="outbound",
                    source_memory_id="source",
                    target_memory_id="other",
                    relation="supports",
                ),
                MemoryEdge(
                    id="inbound",
                    source_memory_id="other",
                    target_memory_id="source",
                    relation="refines",
                ),
                MemoryEdge(
                    id="unrelated",
                    source_memory_id="target",
                    target_memory_id="other",
                    relation="links",
                ),
                FactAtomic(
                    id="fact",
                    statement="甲与乙协作完成丙项目",
                    predicate="collaborates",
                    source_memory_id="source",
                ),
                FactEntity(fact_id="fact", entity_name="甲", role="subject"),
            ]
        )
        session.commit()


def _snapshot(engine):
    """从独立 session 回读全部被路由改变的数据面。"""
    with Session(engine) as session:
        tables = (MemoryItem, MemoryEdge, FactAtomic, FactEntity, MemoryAuditEvent)
        snapshot = {
            table.__tablename__: sorted(
                (row.model_dump(mode="json") for row in session.exec(select(table)).all()),
                key=str,
            )
            for table in tables
        }
        snapshot["fts"] = (
            session.connection()
            .exec_driver_sql("SELECT memory_id, content FROM memory_fts ORDER BY memory_id")
            .all()
        )
        return snapshot


def _call(operation, memory_id="source"):
    if operation == "get":
        return terminal.get_single_memory(memory_id, principal=_OWNER)
    if operation == "update":
        return terminal.update_memory(
            memory_id, terminal.MemoryUpdateReq(content="更新后的正文"), principal=_OWNER
        )
    if operation == "delete":
        return terminal.delete_memory(memory_id, principal=_OWNER)
    return terminal.merge_memories(
        terminal.MergeReq(source_id=memory_id, target_id="target"), principal=_OWNER
    )


def test_get_loads_one_authorized_row(terminal_db):
    engine, _ = terminal_db
    _seed(engine)
    statements = []

    def trace_sql(connection, _record, _proxy):
        connection.set_trace_callback(statements.append)

    event.listen(engine, "checkout", trace_sql)
    try:
        result = terminal.get_single_memory("source", principal=_OWNER)
    finally:
        event.remove(engine, "checkout", trace_sql)
        with engine.connect() as connection:
            connection.connection.driver_connection.set_trace_callback(None)
    assert result["content"] == "原始正文-source"
    assert result["user_id"] == "owner"
    memory_reads = [
        sql
        for sql in statements
        if sql.lstrip().upper().startswith("SELECT") and "memoryitem" in sql
    ]
    assert len(memory_reads) == 1, memory_reads


def test_helpers_load_and_check_real_row(terminal_db):
    engine, _ = terminal_db
    _seed(engine)
    with Session(engine) as session:
        row = terminal._memory_or_404(session, "source")
        assert row.content == "原始正文-source"
        terminal._check_ownership(session, "source", _OWNER)
        with pytest.raises(HTTPException) as raised:
            terminal._memory_or_404(session, "absent")
        assert (raised.value.status_code, raised.value.detail) == (404, "memory not found")


@pytest.mark.parametrize("operation", ["get", "update", "delete", "merge"])
@pytest.mark.parametrize(
    "overrides,detail",
    [
        ({"user_id": "someone-else"}, "resource belongs to another user"),
        ({"tenant_id": "another-tenant"}, "resource belongs to another tenant"),
        ({"lane": "secret"}, "lane not allowed for this principal"),
    ],
)
def test_ownership_errors_do_not_change_any_rows(terminal_db, operation, overrides, detail):
    engine, ledger = terminal_db
    _seed(engine, **overrides)
    before = _snapshot(engine)
    with pytest.raises(HTTPException) as raised:
        _call(operation)
    assert (raised.value.status_code, raised.value.detail) == (403, detail)
    assert _snapshot(engine) == before
    assert ledger.added == ledger.deleted == []


@pytest.mark.parametrize("operation", ["get", "update", "delete", "merge"])
def test_missing_rows_keep_404_and_write_nothing(terminal_db, operation):
    engine, ledger = terminal_db
    _seed(engine)
    before = _snapshot(engine)
    with pytest.raises(HTTPException) as raised:
        _call(operation, "absent")
    expected = "source memory absent not found" if operation == "merge" else "memory not found"
    assert (raised.value.status_code, raised.value.detail) == (404, expected)
    assert _snapshot(engine) == before
    assert ledger.added == ledger.deleted == []


def test_update_commits_content_fields_and_fts(terminal_db):
    engine, ledger = terminal_db
    _seed(engine)
    result = terminal.update_memory(
        "source",
        terminal.MemoryUpdateReq(
            content="更新后的正文", importance=0.6, confidence=0.8, memory_type="episodic"
        ),
        principal=_OWNER,
    )
    assert result == {"ok": True, "message": "updated 4 field(s)", "vector_synced": True}
    with Session(engine) as session:
        row = session.get(MemoryItem, "source")
        assert (row.content, row.importance, row.confidence, row.memory_type) == (
            "更新后的正文",
            0.6,
            0.8,
            "episodic",
        )
        assert row.updated_at >= row.created_at
    with engine.connect() as connection:
        assert "source" in search_fts(connection.connection.driver_connection, "更新后的正文")
    assert ledger.added == ["source"]


def test_noop_update_keeps_rows_and_retracted_update_is_rejected(terminal_db):
    engine, ledger = terminal_db
    _seed(engine)
    before = _snapshot(engine)
    assert terminal.update_memory("source", terminal.MemoryUpdateReq(), principal=_OWNER) == {
        "ok": True,
        "message": "no changes",
    }
    assert _snapshot(engine) == before
    with Session(engine) as session:
        row = session.get(MemoryItem, "source")
        row.status = "retracted"
        session.commit()
    before = _snapshot(engine)
    with pytest.raises(HTTPException) as raised:
        _call("update")
    assert raised.value.status_code == 409
    assert _snapshot(engine) == before
    assert ledger.added == []


def test_delete_cascades_both_edges_facts_fts_and_audits(terminal_db):
    engine, ledger = terminal_db
    _seed(engine)
    result = terminal.delete_memory("source", principal=_OWNER)
    assert result == {"ok": True, "fts_removed": True, "vector_removed": True, "warnings": []}
    snapshot = _snapshot(engine)
    assert {row["id"] for row in snapshot["memoryitem"]} == {"target", "other"}
    assert {row["id"] for row in snapshot["memoryedge"]} == {"unrelated"}
    assert snapshot["fact_atomic"] == snapshot["fact_entity"] == []
    assert dict(snapshot["fts"]) == {"target": "原始正文-target", "other": "原始正文-other"}
    with Session(engine) as session:
        audit = session.exec(select(MemoryAuditEvent)).one()
        assert (audit.memory_id, audit.action, audit.actor) == ("source", "delete", "owner")
        assert audit.content_len == len("原始正文-source")
    assert ledger.deleted == ["source"]


def test_merge_repoints_facts_keeps_lineage_and_updates_fts(terminal_db):
    engine, ledger = terminal_db
    _seed(engine)
    result = _call("merge")
    assert result == {
        "ok": True,
        "merged_into": "target",
        "content": "原始正文-target\n---\n原始正文-source",
        "warnings": [],
    }
    with Session(engine) as session:
        assert session.get(MemoryItem, "source") is None
        target = session.get(MemoryItem, "target")
        assert (target.content, target.version, target.importance) == (result["content"], 2, 0.9)
        assert session.get(FactAtomic, "fact").source_memory_id == "target"
        assert session.exec(select(FactEntity)).one().fact_id == "fact"
        edges = session.exec(select(MemoryEdge)).all()
        assert {(e.source_memory_id, e.target_memory_id, e.relation) for e in edges} == {
            ("target", "other", "links"),
            ("target", "source", "supersedes"),
        }
        lineage = next(e for e in edges if e.relation == "supersedes")
        assert (lineage.user_id, lineage.tenant_id) == ("owner", "tenant")
    assert dict(_snapshot(engine)["fts"]) == {
        "target": result["content"],
        "other": "原始正文-other",
    }
    assert ledger.deleted == ["source"] and ledger.added == ["target"]


@pytest.mark.parametrize("missing", ["source", "target", "both"])
def test_merge_missing_source_and_target_precedence(terminal_db, missing):
    engine, _ = terminal_db
    _seed(engine)
    source = "absent-source" if missing in ("source", "both") else "source"
    target = "absent-target" if missing in ("target", "both") else "target"
    with pytest.raises(HTTPException) as raised:
        terminal.merge_memories(
            terminal.MergeReq(source_id=source, target_id=target), principal=_OWNER
        )
    expected = (
        "source memory absent-source not found"
        if missing != "target"
        else "target memory absent-target not found"
    )
    assert (raised.value.status_code, raised.value.detail) == (404, expected)


def test_merge_target_ownership_and_self_merge_rejected_without_writes(terminal_db):
    engine, ledger = terminal_db
    _seed(engine)
    with Session(engine) as session:
        target = session.get(MemoryItem, "target")
        target.user_id = "someone-else"
        session.commit()
    before = _snapshot(engine)
    with pytest.raises(HTTPException) as raised:
        _call("merge")
    assert raised.value.status_code == 403
    with pytest.raises(HTTPException) as raised:
        terminal.merge_memories(
            terminal.MergeReq(source_id="source", target_id="source"), principal=_OWNER
        )
    assert raised.value.status_code == 400
    assert _snapshot(engine) == before
    assert ledger.added == ledger.deleted == []


@pytest.mark.parametrize("operation", ["update", "delete", "merge"])
def test_sqlite_write_failure_rolls_back_every_sql_surface(terminal_db, operation):
    engine, ledger = terminal_db
    _seed(engine)
    before = _snapshot(engine)
    action = "UPDATE" if operation == "update" else "DELETE"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TRIGGER refuse_memory_write BEFORE {action} ON memoryitem "
            "WHEN OLD.id = 'source' BEGIN SELECT RAISE(ABORT, 'test write refusal'); END"
        )
    error = HTTPException if operation == "delete" else IntegrityError
    with pytest.raises(error) as raised:
        _call(operation)
    if operation == "delete":
        assert raised.value.status_code == 500
        assert "rolled back" in raised.value.detail
    assert _snapshot(engine) == before
    assert ledger.added == ledger.deleted == []
