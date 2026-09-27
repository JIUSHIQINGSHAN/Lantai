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
from sqlmodel import Field, SQLModel, create_engine

from lantai.storage.db import _has_column
from lantai.storage.migrations_v022 import apply_v022_migrations


class _OldPromptTemplate(SQLModel, table=True):
    """加列之前的 prompt_template 形态（照 tables.py 改动前复刻）。"""

    __tablename__ = "prompt_template"
    __table_args__ = {"extend_existing": True}

    id: str = Field(primary_key=True)
    template: str = ""
    description: str = ""
    updated_at: str = ""


class _OldSkillCrystal(SQLModel, table=True):
    """加列之前的 skill_crystal 形态。"""

    __tablename__ = "skill_crystal"
    __table_args__ = {"extend_existing": True}

    id: str = Field(primary_key=True)
    skill_name: str = Field(default="", index=True)
    trigger_rule: str = ""
    procedure: str = ""
    status: str = "candidate"


@pytest.fixture()
def legacy_db(tmp_path: Path):
    """一个按**老 schema**建好的 SQLite 文件库（没有归属列）。"""
    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file}")
    _OldPromptTemplate.metadata.create_all(engine)
    _OldSkillCrystal.metadata.create_all(engine)
    return engine


def _columns(engine, table: str) -> list[str]:
    """真查 PRAGMA table_info（raw connection 只吃 str，不吃 text()）。"""
    conn = engine.raw_connection()
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


class TestOwnershipMigration:
    def test_legacy_db_gains_ownership_columns(self, legacy_db):
        """老库跑完迁移，两张表都有 tenant_id / user_id / agent_id。"""
        # 前置断言：迁移前确实没有（否则这条测试什么都没验）
        assert "user_id" not in _columns(legacy_db, "prompt_template")
        assert "user_id" not in _columns(legacy_db, "skill_crystal")

        apply_v022_migrations(legacy_db)

        for table in ("prompt_template", "skill_crystal"):
            cols = _columns(legacy_db, table)
            for col in ("tenant_id", "user_id", "agent_id"):
                assert col in cols, f"{table} 缺 {col}：{cols}"

    def test_migration_is_idempotent(self, legacy_db):
        """跑两遍不报错、不重复加列（app 每次启动都会调）。"""
        apply_v022_migrations(legacy_db)
        before = {t: _columns(legacy_db, t) for t in ("prompt_template", "skill_crystal")}

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
            assert _has_column(conn, "prompt_template", "user_id")
            assert _has_column(conn, "skill_crystal", "agent_id")
            assert not _has_column(conn, "prompt_template", "nonexistent_col")
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

    def test_missing_table_does_not_break_startup(self, tmp_path):
        """表不存在时迁移只记日志不抛（降级而非崩溃，启动路径必须稳）。"""
        engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        # 不建任何表，直接跑——不该抛异常
        apply_v022_migrations(engine)
