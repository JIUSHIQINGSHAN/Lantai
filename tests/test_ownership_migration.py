"""归属列增量迁移的不 mock 冒烟测试（票 .scratch/readside-gaps/11）。

**为什么单独一个文件**：`apply_v022_migrations` 是 `api.app` 启动时
必经的核心函数，改的是**真实 SQLite DDL**（`ALTER TABLE ADD COLUMN`）。
此前它一条测试都没有——按 AGENTS.md 测试纪律，核心函数必须至少有一个
不 mock 的冒烟测试。

**这里不 mock 任何东西**：真建一个 SQLite 文件库，先按"老 schema"
（没有归属列）建表，再跑迁移，然后真查 `PRAGMA table_info` 看列在不在。
mocking 掉 engine 就等于跳过了迁移的内部计算逻辑——正是纪律里
明文禁止的那类 mock。

三个判据：
1. 老库（无归属列）跑完迁移，`prompt_template` / `skill_crystal` 有列。
2. 新库（已有列）再跑一遍不报错、不重复加列（幂等——启动每次都会调）。
3. 迁移后 ORM 能真写真读带归属列的行（证明列与模型对齐，
   不是"列加了但模型字段名写错"那种假通过）。
"""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from lantai.storage.db import _has_column
from lantai.storage.migrations_v022 import apply_v022_migrations

# 老 schema 的原始 DDL：四张表全部用裸 SQL 建，**不用 SQLModel 复刻类**。
# 两个原因：
# 1. 真实模型在 `lantai.models.tables` 里都没有 `extend_existing`，测试里
#    再声明同名 table class，import 真实模块时就抛
#    `Table 'xxx' is already defined for this MetaData instance`——
#    整个测试文件收集失败（自己踩过）。
# 2. 也不能用 `.metadata.create_all()`：metadata 全局共享，conftest 一
#    import 真实模型（已带归属列）就都注册进来了，会把它们一起建出来，
#    "迁移前没有归属列"这个前置断言直接假通过。
# 裸 DDL 还更接近迁移真正面对的东西：一个已经存在的库，迁移只往它身上
# `ALTER TABLE ADD COLUMN`。
#
# 表名**不带下划线**：`prompttemplate` / `skillcrystal` 才是真实表名
# （票 11 迁移曾写成 `prompt_template` / `skill_crystal`，真实库没这两张表，
# `_has_column` 对不存在的表返回 True，那次 ALTER 静默不执行）。
_LEGACY_DDL = {
    "prompttemplate": (
        "CREATE TABLE prompttemplate ("
        "id VARCHAR PRIMARY KEY, template VARCHAR NOT NULL, "
        "description VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)"
    ),
    "skillcrystal": (
        "CREATE TABLE skillcrystal ("
        "id VARCHAR PRIMARY KEY, skill_name VARCHAR NOT NULL, "
        "trigger_rule VARCHAR NOT NULL, procedure VARCHAR NOT NULL, "
        "status VARCHAR NOT NULL)"
    ),
    "source": (
        "CREATE TABLE source ("
        "id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, "
        "config JSON NOT NULL, enabled BOOLEAN NOT NULL, "
        "trust_score FLOAT NOT NULL, last_fetched_at TIMESTAMP)"
    ),
    "retrieval_event": (
        "CREATE TABLE retrieval_event ("
        "id VARCHAR PRIMARY KEY, session_id VARCHAR, trace_id VARCHAR NOT NULL, "
        "query_text VARCHAR NOT NULL, query_norm_hash VARCHAR NOT NULL, "
        "lane VARCHAR NOT NULL, intent_bucket VARCHAR, "
        "param_snapshot_hash VARCHAR NOT NULL, result_ids JSON NOT NULL, "
        "result_scores JSON NOT NULL, used_ids JSON NOT NULL, "
        "latency_ms INTEGER NOT NULL, zero_result BOOLEAN NOT NULL, "
        "scene_ids JSON NOT NULL, estimated_tokens INTEGER NOT NULL, "
        "is_system_noise BOOLEAN NOT NULL, request_id VARCHAR, "
        "receipt_status VARCHAR NOT NULL, receipt_at TIMESTAMP, "
        "created_at TIMESTAMP NOT NULL)"
    ),
}


@pytest.fixture()
def legacy_db(tmp_path: Path):
    """一个按**老 schema**建好的 SQLite 文件库（没有归属列）。"""
    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file}")
    for ddl in _LEGACY_DDL.values():
        with engine.begin() as conn:
            conn.execute(text(ddl))
    return engine


def _columns(engine, table: str) -> list[str]:
    """真查 PRAGMA table_info（raw connection 只吃 str，不吃 text()）。"""
    conn = engine.raw_connection()
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


def _migration_tables() -> list[str]:
    """从迁移源码里抓出归属列那张循环的表名清单（不 import 执行，纯读）。

    抓源码而不是抓常量：迁移里就是一组字面量 tuple，改表名时会改到它，
    而测试要盯的正是这组字面量。
    """
    import re

    src = (
        Path(__file__)
        .parent.parent.joinpath("lantai", "storage", "migrations_v022.py")
        .read_text(encoding="utf-8")
    )
    m = re.search(r"for table in \(([^)]*)\)", src)
    assert m, "迁移里找不到 `for table in (...)` 那个循环"
    return re.findall(r'"([^"]+)"', m.group(1))


ALL_TABLES = ("prompttemplate", "skillcrystal", "source", "retrieval_event")


class TestOwnershipMigration:
    def test_migration_table_names_match_models(self):
        """迁移里的表名必须与 `Model.__tablename__` 逐字一致。

        票 11 回归：迁移写的是 `prompt_template` / `skill_crystal`（带下划线），
        而真实库表叫 `prompttemplate` / `skillcrystal`。`_has_column`
        对不存在的表返回 True，所以写错表名**不报错**，只是那次
        `ALTER TABLE` 静默不执行——归属列一个都没加，读侧
        `_prompt_scope` / `_crystal_scope` 查不存在的列直接 500。
        这条测试就是那道防线：从源码里把表名抓出来跟模型对。
        """
        import lantai.models.tables as m

        expected = {
            "prompt_template": m.PromptTemplate.__tablename__,
            "skill_crystal": m.SkillCrystal.__tablename__,
            "source": m.Source.__tablename__,
            "retrieval_event": m.RetrievalEvent.__tablename__,
        }
        actual = _migration_tables()
        assert set(actual) == set(expected.values()), (
            f"迁移表名与模型不一致：迁移={sorted(actual)} 模型={sorted(expected.values())}"
        )
        for stale, correct in expected.items():
            if stale != correct:
                assert stale not in actual, f"迁移还在用不存在的表名 {stale}（正确是 {correct}）"

    def test_legacy_db_gains_ownership_columns(self, legacy_db):
        """老库跑完迁移，四张表都有 tenant_id / user_id / agent_id。"""
        # 前置断言：迁移前确实没有（否则这条测试什么都没验）
        for table in ALL_TABLES:
            assert "user_id" not in _columns(legacy_db, table), f"{table} 迁移前就有 user_id"

        apply_v022_migrations(legacy_db)

        for table in ALL_TABLES:
            cols = _columns(legacy_db, table)
            for col in ("tenant_id", "user_id", "agent_id"):
                assert col in cols, f"{table} 缺 {col}：{cols}"

    def test_legacy_db_gains_ownership_columns_each_table(self, legacy_db):
        """逐表断言（上面那条把四张表放进同一个循环，漏一张看不出来）。"""
        apply_v022_migrations(legacy_db)
        for table in ALL_TABLES:
            cols = _columns(legacy_db, table)
            assert "user_id" in cols, f"{table} 没加上归属列：{cols}"

    def test_migration_is_idempotent(self, legacy_db):
        """跑两遍不报错、不重复加列（app 每次启动都会调）。"""
        apply_v022_migrations(legacy_db)
        before = {t: _columns(legacy_db, t) for t in ALL_TABLES}

        apply_v022_migrations(legacy_db)  # 第二遍不该炸

        for t, cols in before.items():
            after = _columns(legacy_db, t)
            assert after == cols, f"{t} 第二遍后列变了：{before[t]} → {after}"
            assert len(after) == len(set(after)), f"{t} 出现重复列：{after}"

    def test_has_column_helper_agrees(self, legacy_db):
        """`_has_column` 与 PRAGMA 一致（迁移自己就靠它判幂等）。"""
        apply_v022_migrations(legacy_db)
        conn = legacy_db.raw_connection()
        try:
            assert _has_column(conn, "prompttemplate", "user_id")
            assert _has_column(conn, "skillcrystal", "agent_id")
            assert not _has_column(conn, "prompttemplate", "nonexistent_col")
            assert not _has_column(conn, "source", "nonexistent_col")
        finally:
            conn.close()

    def test_has_column_nonexistent_table_returns_true(self):
        """`_has_column` 对**不存在的表**返回 True——这是票 11 事故的根。

        迁移靠它判幂等，所以"表名写错"不会报错，只会让那次
        `ALTER TABLE` 静默不执行。这条测试把这个行为钉住：
        改它之前先看懂上面的迁移为什么必须逐字对齐表名。
        """
        engine = create_engine("sqlite:///:memory:")
        conn = engine.raw_connection()
        try:
            assert _has_column(conn, "prompt_template", "user_id") is True
            assert _has_column(conn, "这张表根本不存在", "user_id") is True
        finally:
            conn.close()

    def test_orm_can_write_read_after_migration(self, legacy_db):
        """迁移后 ORM 能真写真读带归属的行——证明列与模型字段对齐。

        这条专门抓"列加了但模型字段名写错"那类假通过：光看 PRAGMA
        有列不够，得让真实模型把 user_id 写进去再读出来。
        """
        apply_v022_migrations(legacy_db)

        from sqlmodel import Session

        from lantai.models.tables import PromptTemplate

        with Session(legacy_db) as s:
            s.add(PromptTemplate(id="p1", template="t", user_id="user-A"))
            s.commit()
            row = s.get(PromptTemplate, "p1")
            assert row is not None
            assert row.user_id == "user-A", f"归属没落进去：{row.user_id}"

    def test_orm_source_and_event_roundtrip(self, legacy_db):
        """`source` / `retrieval_event` 迁移后也能真写真读归属列（票 10）。

        这两张表是新加的——列在 DDL 里有了，但模型字段名写错的话，
        ORM 一写就炸。这条就是那道防线。
        """
        apply_v022_migrations(legacy_db)

        from sqlmodel import Session

        from lantai.core.time import utcnow
        from lantai.models.tables import RetrievalEvent, Source

        with Session(legacy_db) as s:
            s.add(
                Source(
                    id="src1",
                    kind="http",
                    config={"url": "http://x"},
                    enabled=True,
                    user_id="user-A",
                )
            )
            s.add(
                RetrievalEvent(
                    id="ev1",
                    trace_id="tr",
                    query_text="q",
                    query_norm_hash="h",
                    lane="fact",
                    param_snapshot_hash="p",
                    user_id="user-A",
                    created_at=utcnow(),
                )
            )
            s.commit()
            src = s.get(Source, "src1")
            ev = s.get(RetrievalEvent, "ev1")
            assert src is not None and src.user_id == "user-A", f"source 归属没落进去：{src}"
            assert ev is not None and ev.user_id == "user-A", f"event 归属没落进去：{ev}"

    def test_missing_table_does_not_break_startup(self, tmp_path):
        """表不存在时迁移只记日志不抛（降级而非崩溃，启动路径必须稳）。"""
        engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        # 不建任何表，直接跑——不该抛异常
        apply_v022_migrations(engine)
