"""衰减类测试：推断规则 / 半衰期 / procedural 永不衰减 / set_decay_class / 迁移"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import remembrance.storage.db as db_module
from remembrance.core.ids import new_id
from remembrance.core.time import utcnow
from remembrance.memory.decay_class import (
    DECAY_CLASS_HALFLIFE, decay_multiplier, infer_decay_class,
)
from remembrance.models.tables import MemoryCheckpoint, MemoryItem


class TestInfer:
    def test_explicit_metadata_wins(self):
        assert infer_decay_class("标题", "内容", {"decay_class": "semantic"}) == "semantic"

    def test_explicit_metadata_ignores_invalid(self):
        assert infer_decay_class("标题", "内容", {"decay_class": "bogus"}) == "episodic"

    def test_procedural_hint(self):
        assert infer_decay_class("铁律", "永远不要删除用户数据") == "procedural"
        assert infer_decay_class("", "必须每天备份") == "procedural"

    def test_semantic_hint(self):
        assert infer_decay_class("配置", "默认开启通知") == "semantic"

    def test_fallback_episodic(self):
        assert infer_decay_class("随便聊聊", "今天天气不错") == "episodic"


class TestMultiplier:
    def test_procedural_never_decays(self):
        assert decay_multiplier("procedural", 3650.0) == 1.0

    def test_halflife_semantic(self):
        assert decay_multiplier("semantic", 180.0) == pytest.approx(0.5)

    def test_halflife_episodic(self):
        assert decay_multiplier("episodic", 30.0) == pytest.approx(0.5)

    def test_negative_age_clamped(self):
        assert decay_multiplier("episodic", -5.0) == 1.0

    def test_unknown_class_defaults_episodic(self):
        assert decay_multiplier("nope", 30.0) == pytest.approx(0.5)


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:",
                      connect_args={"check_same_thread": False},
                      poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


def _add_mem(engine, *, content="测试内容", lane="general",
             decay_class="episodic", last_used_days_ago=0.0,
             importance=0.5, use_count=0) -> MemoryItem:
    mid = new_id("mem")
    m = MemoryItem(
        id=mid, memory_type="general", key=mid, content=content,
        lane=lane, status="active", importance=importance, use_count=use_count,
        decay_score=1.0, decay_class=decay_class,
        last_used_at=utcnow() - timedelta(days=last_used_days_ago),
        created_at=utcnow() - timedelta(days=last_used_days_ago))
    with Session(engine) as s:
        s.add(m)
        s.commit()
        s.refresh(m)  # commit 后属性过期，refresh 使 detached 后可读
    return m


class TestApplyForgetting:
    def test_procedural_never_archived_even_very_old(self, engine):
        """procedural 记忆老化后仍保持 decay_score=1.0 且 active（冒烟直调）"""
        _add_mem(engine, decay_class="procedural", last_used_days_ago=9999.0)
        with patch.object(db_module, "get_session", lambda: Session(engine)):
            from remembrance.memory import forgetting
            forgetting.apply_forgetting()
        with Session(engine) as s:
            m = s.exec(select(MemoryItem)).first()
            assert m.decay_score == 1.0
            assert m.status == "active"

    def test_episodic_still_decays(self, engine):
        """对照：episodic 老化 9999 天 → 衰减 + 归档"""
        _add_mem(engine, decay_class="episodic", last_used_days_ago=9999.0)
        with patch.object(db_module, "get_session", lambda: Session(engine)):
            from remembrance.memory import forgetting
            forgetting.apply_forgetting()
        with Session(engine) as s:
            m = s.exec(select(MemoryItem)).first()
            assert m.decay_score < 0.01
            assert m.status == "archived"


class TestSetDecayClass:
    def test_set_and_checkpoint(self, engine):
        from remembrance.services import memory_service as ms
        m = _add_mem(engine)
        with patch.object(db_module, "get_session", lambda: Session(engine)):
            res = ms.set_decay_class(m.id, "procedural")
        assert res == {"ok": True, "memory_id": m.id, "decay_class": "procedural"}
        with Session(engine) as s:
            mem = s.get(MemoryItem, m.id)
            assert mem.decay_class == "procedural"
            ckpt = s.exec(select(MemoryCheckpoint)).first()
            assert ckpt is not None
            assert ckpt.before == {"decay_class": "episodic"}
            assert ckpt.trigger == "decay_class"

    def test_invalid_class_rejected(self, engine):
        from remembrance.services import memory_service as ms
        m = _add_mem(engine)
        with pytest.raises(ValueError):
            with patch.object(db_module, "get_session", lambda: Session(engine)):
                ms.set_decay_class(m.id, "bogus")

    def test_missing_memory(self, engine):
        from remembrance.services import memory_service as ms
        with patch.object(db_module, "get_session", lambda: Session(engine)):
            res = ms.set_decay_class("no-such-id", "procedural")
        assert res["ok"] is False


class TestMigration:
    def test_init_db_adds_decay_class_to_legacy_table(self):
        """老库（无 decay_class 列）→ init_db 幂等迁移加列；重复执行不炸"""
        from unittest.mock import patch as _patch
        from sqlalchemy import text

        legacy = create_engine("sqlite:///:memory:")
        with legacy.begin() as c:
            c.execute(text(
                "CREATE TABLE memoryitem (id TEXT PRIMARY KEY, memory_type TEXT, "
                "key TEXT, content TEXT, lane TEXT, status TEXT, decay_score FLOAT)"))
        # 迁移前无 decay_class
        cols_before = [r[1] for r in legacy.raw_connection()
                       .execute("PRAGMA table_info(memoryitem)")]
        assert "decay_class" not in cols_before

        with _patch.object(db_module, "engine", legacy):
            db_module.init_db()  # 第一次：加列（内存库建表 + FTS）
        cols_after = [r[1] for r in legacy.raw_connection()
                      .execute("PRAGMA table_info(memoryitem)")]
        assert "decay_class" in cols_after

        with _patch.object(db_module, "engine", legacy):
            db_module.init_db()  # 第二次：duplicate column 被幂等吞掉
        cols_again = [r[1] for r in legacy.raw_connection()
                      .execute("PRAGMA table_info(memoryitem)")]
        assert "decay_class" in cols_again
