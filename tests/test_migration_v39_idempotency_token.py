"""v39: 老库 idempotency_keys 缺 token 列时，迁移链必须补上（票 74）。

勘合所有权令牌与时间解耦（v39 起 claim/接管写 uuid4 token，条件语句按
token 判据）：此前令牌即 created_at 裸 epoch 浮点，粗刻度时钟下相邻两次
取时可同值，接管把新主行 created_at 改写回与旧令牌相等时，旧主带令牌
release/settle 的条件删除命中新主行——重试即双执行。

与 test_migration_v36 同一约定：不 mock，真实临时 SQLite + 真实
apply_migrations；升级窗老行（token NULL）的引擎侧语义锁见
tests/test_idempotency_receipt.py::TestLegacyTokenCompat。
"""

import sqlite3

from sqlmodel import Session, SQLModel, create_engine, select

from lantai.storage.migrations import (
    TARGET_SCHEMA_VERSION,
    apply_migrations,
    get_migration_failures,
)


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _make_legacy_db(path):
    """造一个「已迁到 v38 的老库」：结构齐平但 idempotency_keys 没有 token。"""
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(path))
    conn.execute("ALTER TABLE idempotency_keys DROP COLUMN token")
    conn.execute("PRAGMA user_version = 38")
    conn.commit()
    conn.close()


def test_legacy_db_missing_idempotency_token_is_restored(tmp_path):
    db_path = tmp_path / "old.db"
    _make_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # 前置：复现漂移——版本已是上一目标值，列却不存在
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 38
        assert "token" not in _columns(conn, "idempotency_keys")

        apply_migrations(conn)
        conn.commit()

        assert TARGET_SCHEMA_VERSION >= 39, (
            f"迁移链目标版本仍是 {TARGET_SCHEMA_VERSION}，没有为令牌列开台阶"
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        assert "token" in _columns(conn, "idempotency_keys"), (
            "老库迁到目标版本后 idempotency_keys.token 仍然缺失：所有权令牌列没迁移"
        )
    finally:
        conn.close()


def test_upgraded_legacy_db_writes_token_via_orm(tmp_path):
    """迁移后的老库必须能真实写入/读回 token（不 mock，冒烟）。"""
    from lantai.models.tables import IdempotencyKey

    db_path = tmp_path / "old2.db"
    _make_legacy_db(db_path)
    conn = sqlite3.connect(str(db_path))
    apply_migrations(conn)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with Session(engine) as s:
        s.add(
            IdempotencyKey(
                key="k1", user_id="default", tenant_id="", fingerprint="fp", token="tok123"
            )
        )
        s.commit()
        got = s.exec(select(IdempotencyKey).where(IdempotencyKey.key == "k1")).first()
        assert got is not None
        assert got.token == "tok123"
    engine.dispose()


def test_fresh_db_has_column_and_chain_is_idempotent(tmp_path):
    """全新库 create_all 天然有该列；重复跑迁移链必须幂等、无失败记录。"""
    from lantai.models.tables import IdempotencyKey

    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(str(db_path))
    try:
        assert "token" in _columns(conn, "idempotency_keys")
        apply_migrations(conn)
        apply_migrations(conn)  # 列已存在时必须跳过，不得抛
        conn.commit()
        assert get_migration_failures() == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
    finally:
        conn.close()

    with Session(create_engine(f"sqlite:///{db_path.as_posix()}")) as s:
        s.add(
            IdempotencyKey(
                key="k2", user_id="default", tenant_id="", fingerprint="fp", token="tok456"
            )
        )
        s.commit()
        got = s.exec(select(IdempotencyKey).where(IdempotencyKey.key == "k2")).first()
        assert got is not None
        assert got.token == "tok456"
