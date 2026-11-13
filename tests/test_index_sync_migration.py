"""票 25：模型索引 ↔ 库索引对账——迁移步幂等索引同步（单一真源 = SQLModel.metadata）。

病灶：索引只有两条出生路径——①表**首次创建**时 create_all 随表落索引
（SQLAlchemy 对已存在的表整体跳过，**索引不会补**）；②迁移链手写
CREATE INDEX IF NOT EXISTS。升级库（remembrance.db 形态：身份时代之前的
存量库）经迁移链 ALTER 补列后，身份列以**无索引形态**进入每条检索的
WHERE 过滤，且无任何机制日后补上——模型声明 156 个索引、迁移链手写仅
覆盖 27 组，真实缺口 122+ 个（立案探针 `_v068_probe_r5_index_gap.py`）。

形态：真 SQLite（tmp_path 文件库）+ 真 `init_db` 全链（create_all + 迁移链
+ FTS），零 mock。「索引建立失败」用**同名表占位**注入（CREATE INDEX 撞同
命名空间的表 → 真实 OperationalError），不替任何被测逻辑。
"""

from sqlmodel import SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.parameters.trust_models  # noqa: F401  参数信任面表同批注册
import lantai.storage.db as db_module
from lantai.storage.db import init_db
from lantai.storage.migrations import TARGET_SCHEMA_VERSION


def _model_index_names():
    """模型声明索引的唯一真源：{表名: {索引名}}。"""
    out = {}
    for table in SQLModel.metadata.tables.values():
        names = {ix.name for ix in table.indexes}
        if names:
            out[table.name] = names
    return out


def _db_index_names(engine):
    """库内索引实况（{表名: {索引名}}，容忍并剔除 sqlite_autoindex）。"""
    conn = engine.raw_connection()
    try:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, set] = {}
    for name, tbl in rows:
        if name.startswith("sqlite_autoindex"):
            continue
        out.setdefault(tbl, set()).add(name)
    return out


def _memoryitem_columns(engine):
    conn = engine.raw_connection()
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(memoryitem)").fetchall()}
    finally:
        conn.close()


def _user_version(engine):
    conn = engine.raw_connection()
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _make_upgrade_db(path):
    """存量升级库形态（remembrance.db 同构）：表在（create_all）、索引全部
    剥离（模拟「建库当时模型里已有」之外全缺——现库实证恰为 ix_memoryitem_
    key/lane/memory_type/namespace 四个、身份索引全缺）、memoryitem 身份四列
    缺失（由迁移链 ALTER 补回）。"""
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    conn = engine.raw_connection()
    try:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%'"
        ).fetchall():
            conn.execute(f'DROP INDEX "{name}"')
        for col in ("user_id", "tenant_id", "agent_id", "session_id"):
            conn.execute(f"ALTER TABLE memoryitem DROP COLUMN {col}")
        conn.commit()
    finally:
        conn.close()
    return engine


def test_upgrade_db_regains_model_indexes(tmp_path, monkeypatch):
    """红测主臂：存量库（索引剥离 + 身份列缺失）跑真 init_db 全链后，
    sqlite_master 索引集 ⊇ 模型声明索引集（逐表比对名集，容忍 sqlite_autoindex）。"""
    engine = _make_upgrade_db(tmp_path / "upgrade.db")

    # 假绿防线：fixture 确实缺 ix_memoryitem_user_id 与身份列（先红后绿的真实分界）
    before = _db_index_names(engine)
    assert "ix_memoryitem_user_id" not in before.get("memoryitem", set())
    assert "user_id" not in _memoryitem_columns(engine), "fixture 未模拟缺身份列，红测失去分界"

    monkeypatch.setattr(db_module, "engine", engine)
    init_db()

    model = _model_index_names()
    got = _db_index_names(engine)
    missing = {
        t: sorted(want - got.get(t, set())) for t, want in model.items() if want - got.get(t, set())
    }
    assert not missing, (
        f"升级库索引缺口（票 25）：create_all 对存量表整体跳过、迁移链不补——{missing}"
    )
    assert _user_version(engine) == TARGET_SCHEMA_VERSION
    # 身份列已由迁移链补回（fixture 语义闭环：列补得上、索引也补得上）
    assert {"user_id", "tenant_id", "agent_id", "session_id"} <= _memoryitem_columns(engine)


def test_fresh_db_indexes_idempotent(tmp_path, monkeypatch):
    """全新库现状锁：fresh 库跑 init_db 索引集 ⊇ 模型声明集；重跑 init_db
    索引集一字不变（同步步幂等——不因它多出或减少任何索引）。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db_module, "engine", engine)
    init_db()
    first = _db_index_names(engine)
    model = _model_index_names()
    missing = {
        t: sorted(want - first.get(t, set()))
        for t, want in model.items()
        if want - first.get(t, set())
    }
    assert not missing, f"全新库缺模型索引：{missing}"
    init_db()
    second = _db_index_names(engine)
    assert first == second, (
        "重跑 init_db 索引集漂移（票 25 幂等破坏）："
        f"仅首跑有 {set(_flatten(first)) - set(_flatten(second))}、仅二跑有 "
        f"{set(_flatten(second)) - set(_flatten(first))}"
    )


def _flatten(by_table):
    return {name for names in by_table.values() for name in names}


def test_single_index_failure_does_not_block_chain(tmp_path, monkeypatch, caplog):
    """假绿防线：人为让一个索引建立失败（同名表占位——CREATE INDEX 撞同命名
    空间的表，真实 OperationalError），不阻断迁移链：版本仍到 TARGET、其余
    索引照常补建、失败留痕可见（不得静默吞——宁 miss 不脏写的留痕面）。"""
    engine = _make_upgrade_db(tmp_path / "broken.db")
    conn = engine.raw_connection()
    try:
        conn.execute("CREATE TABLE ix_memoryitem_lane (x INTEGER)")
        conn.commit()
    finally:
        conn.close()

    import logging

    with caplog.at_level(logging.WARNING, logger="lantai"):
        monkeypatch.setattr(db_module, "engine", engine)
        init_db()

    assert _user_version(engine) == TARGET_SCHEMA_VERSION, (
        "单索引建立失败不得阻断迁移链（票 25：version 只在整块成功后推进，"
        "索引级失败必须在块内隔离留痕）"
    )
    got = _db_index_names(engine)
    assert "ix_memoryitem_user_id" in got.get("memoryitem", set()), (
        "一个索引失败连带其余索引没建（票 25：失败必须逐索引隔离）"
    )
    assert any("ix_memoryitem_lane" in r.getMessage() for r in caplog.records), (
        "索引建立失败被静默吞掉（票 25：warning 留痕是硬要求，运维须可见首启变慢原因）"
    )
