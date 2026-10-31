"""N-03 残留修复：考功写副作用身份闸（票 `.scratch/v0.64-next-window-2026-11/09`）。

病灶：review 报告主雷（无身份降权所有人记忆）已被 `_owned_scope` 挡住，
但**残留可达面**：`principal=None`（MCP `user_id` 不透传）+ NULL 属主行
（真实库 391/635 条 verbatim 全 NULL）→ `_owned_scope` 的 `IS NULL` 半边
放进候选 → `_check_access(owner_id=None)=True` → 高频低效 NULL 行被
评估并**降权写回**（实测 0.5 → 0.1）。

修法：`principal is None` = 调用方身份未知，写路径按「宁 miss 不脏写」
不落笔——`run_cycle` 空转返回 `skipped_no_principal` 标注。系统身份
（SYSTEM_VIEWER/admin）行为不变；调用面普查（票面）：REST×2 ctx 必有
身份、MCP×1 None 可达、无 scheduler/worker 调用——空转不伤任何定时任务。

不 mock 冒烟：真实 MemoryItem + 真实 SQLite，直调 `run_kaogong_cycle`。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401
from lantai.models.tables import MemoryItem


@pytest.fixture
def kaogong_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    import lantai.storage.db as db_mod

    monkeypatch.setattr(db_mod, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def _seed_null_owner_low_efficiency(engine):
    """NULL 属主 + 高频低效（use_count=10, helpful=1, ratio=0.1 ≤ 0.2）→ demote 目标。"""
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="mem-null-owner",
                content="无主高频低效记忆",
                user_id=None,
                status="active",
                tier="working",
                use_count=10,
                helpful_count=1,
                importance=0.5,
                decay_score=0.5,
                decay_class="episodic",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        s.commit()


class TestKaogongNoPrincipalWriteGuard:
    def test_none_principal_does_not_demote_null_owner_row(self, kaogong_env):
        """核心复现：principal=None 不得降权写回 NULL 属主行（修复前红：0.5→0.1）。"""
        from lantai.services.kaogong_service import run_kaogong_cycle

        _seed_null_owner_low_efficiency(kaogong_env)
        out = run_kaogong_cycle(principal=None)
        with Session(kaogong_env) as s:
            m = s.get(MemoryItem, "mem-null-owner")
            assert m.importance == 0.5, f"无身份调用降权写回了 NULL 属主行: {out}"
        assert out.get("skipped_no_principal") is True, out

    def test_system_viewer_still_evaluates_and_demotes(self, kaogong_env):
        """系统身份（SYSTEM_VIEWER）行为不变：评估照常、降权落笔（既有语义零回归）。"""
        from lantai.core.acl import SYSTEM_VIEWER, Principal
        from lantai.services.kaogong_service import run_kaogong_cycle

        _seed_null_owner_low_efficiency(kaogong_env)
        out = run_kaogong_cycle(principal=Principal(user_id=SYSTEM_VIEWER))
        assert out.get("skipped_no_principal") is not True, out
        assert out["evaluated"] >= 1, out
        with Session(kaogong_env) as s:
            m = s.get(MemoryItem, "mem-null-owner")
            assert m.importance == 0.1, f"系统身份应降权至 0.1: {out}"

    def test_named_owner_row_still_blocked_for_none(self, kaogong_env):
        """具名他人行（主雷面）维持阻断——回归对照。"""
        from lantai.services.kaogong_service import run_kaogong_cycle

        with Session(kaogong_env) as s:
            s.add(
                MemoryItem(
                    id="mem-named",
                    content="他人高频低效记忆",
                    user_id="other-user",
                    status="active",
                    tier="working",
                    use_count=10,
                    helpful_count=1,
                    importance=0.5,
                    decay_score=0.5,
                    decay_class="episodic",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            s.commit()
        run_kaogong_cycle(principal=None)
        with Session(kaogong_env) as s:
            m = s.get(MemoryItem, "mem-named")
            assert m.importance == 0.5
