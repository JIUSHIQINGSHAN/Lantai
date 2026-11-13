"""票 60：测候影子视窗内存注册表——模块级 dict 无属主校验可被任意覆盖夺权、重启清零。

`.scratch/v0.68-next-window-2026-11/issues/60-cehou-shadow-window-registry.md`

**先说影响**（`lantai/cognition/cehou.py::_SHADOW_WINDOWS` 进程内模块级 dict）：

| 病灶 | 修复前 |
|---|---|
| `_SHADOW_WINDOWS[window_id] = data` 无条件覆盖 | A 用户可用同 window_id 覆盖 B 用户（或 worker）正在观察的视窗，篡改他人预测实验对照组 |
| 模块级全局无持久化 | uvicorn 多 worker 各一份（读取随机命中）；进程重启全部视窗蒸发，按天观察的长期实验全丢 |
| cehou_service docstring 自称「持久化」 | 实际只做注册表转发，名实不符 |

**修法**：视窗状态落库（新表 `CehouWindow`，window_id 主键）；`create_observation_window`
按 window_id upsert 且属主校验——已存在且非本人（admin/SYSTEM_VIEWER 除外）→ 拒绝
HTTPException(409)，不静默覆盖（宁 miss 不脏写）；owner 恒 `viewer_of` 收敛（None→
"default"，不再落 NULL 属主窗，票 57 口径）。多 worker 一致性与重启恢复随落库自动解决。

**重启模拟说明**（不 mock 被测链路）：病灶 2 的「重启清零」在本进程内无法自然复现，
测试用 `_SHADOW_WINDOWS.clear()` 模拟进程死亡——这是复现重启事件，不是替被测函数
跳过内部计算；修复后该属性已删除，`getattr` 兜底使其自然空转，持久臂改由真库
（StaticPool 单连接跨 Session 存活）承载。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cognition.cehou import CehouEngine
from lantai.core.acl import Principal


def _p(user_id: str | None):
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=None, role="user")


@pytest.fixture()
def test_db():
    """真 SQLite 内存库 + get_session 指向它（不 mock 计算逻辑）。

    StaticPool 单连接：所有 Session 共享同一连接，「新引擎实例」读到的就是
    刚写入的行——重启语义由「清掉进程内注册表 + 新实例」承载，见模块 docstring。
    """
    import lantai.models.tables  # noqa: F401  注册全部表

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    original = db_module.get_session

    def session_factory() -> Session:
        return Session(eng)

    db_module.get_session = session_factory
    yield Session(eng)
    # 必须还原成原来的可调用对象（同 test_mcp_none_scope_leaks 教训）
    db_module.get_session = original
    eng.dispose()


def _simulate_process_restart() -> None:
    """模拟进程死亡：清掉模块级注册表（修复前是唯一真源；修复后属性已删除，空转）。"""
    import lantai.cognition.cehou as cehou_mod

    registry = getattr(cehou_mod, "_SHADOW_WINDOWS", None)
    if registry is not None:
        registry.clear()


class TestShadowWindowOwnership:
    """修复目标 1（TDD 要求 1）：覆盖夺权——同 window_id 非本人再开必须拒绝。"""

    def test_conflicting_owner_cannot_overwrite_window(self, test_db):
        engine_a = CehouEngine(principal=_p("user-a"))
        engine_a.create_observation_window(
            window_id="w-1", target_type="param", target_id="target-of-a"
        )

        engine_b = CehouEngine(principal=_p("user-b"))
        with pytest.raises(HTTPException) as exc_info:
            engine_b.create_observation_window(
                window_id="w-1", target_type="param", target_id="target-of-b"
            )
        assert exc_info.value.status_code == 409

        # A 的实验对照组原样无损——不是被 B 的内容覆盖
        view = CehouEngine(principal=_p("user-a")).inspect_window(window_id="w-1")
        assert view is not None
        assert view["target_id"] == "target-of-a"

    def test_same_owner_reregister_is_upsert(self, test_db):
        engine_a = CehouEngine(principal=_p("user-a"))
        engine_a.create_observation_window(
            window_id="w-own", target_type="param", target_id="t1", required_days=7
        )
        engine_a.record_sample(window_id="w-own", success=True)
        # 同属主重开 = 重新注册本 ID 实验（upsert），不拒绝也不串到他人
        engine_a.create_observation_window(
            window_id="w-own", target_type="param", target_id="t1", required_days=14
        )
        view = engine_a.inspect_window(window_id="w-own", mock_days_observed=0)
        assert view is not None
        assert view["state"] == "observing"


class TestShadowWindowPersistence:
    """修复目标 2（TDD 要求 2）：重启恢复——视窗不再随进程内注册表蒸发。"""

    def test_window_survives_process_restart(self, test_db):
        engine = CehouEngine(principal=_p("default"))
        engine.create_observation_window(window_id="w-2", target_type="rule", target_id="rule-1")
        for _ in range(3):
            engine.record_sample(window_id="w-2", success=True, latency_ms=10.0)

        _simulate_process_restart()

        fresh_engine = CehouEngine(principal=_p("default"))
        view = fresh_engine.inspect_window(window_id="w-2")
        assert view is not None, "进程重启后视窗蒸发——注册表仍是唯一真源，无持久化"
        assert view["sample_count"] == 3
        assert view["state"] == "observing"


class TestShadowWindowMultiInstance:
    """修复目标 2（TDD 要求 3）：双实例写读终态一致。"""

    def test_two_engine_instances_aggregate_samples(self, test_db):
        engine_1 = CehouEngine(principal=_p("default"))
        engine_1.create_observation_window(
            window_id="w-3", target_type="proposal", target_id="prop-1"
        )
        engine_1.record_sample(window_id="w-3", success=True)

        engine_2 = CehouEngine(principal=_p("default"))
        engine_2.record_sample(window_id="w-3", success=False)

        view = CehouEngine(principal=_p("default")).inspect_window(window_id="w-3")
        assert view is not None
        assert view["sample_count"] == 2


class TestShadowWindowRegression:
    """修复目标 3（TDD 要求 4）：主路径行为零变化——应验转正判定语义保持。"""

    def test_graduation_path_unchanged(self, test_db):
        engine = CehouEngine(principal=_p("default"))
        engine.create_observation_window(
            window_id="w-4",
            target_type="param",
            target_id="param-bm25-k1",
            required_days=7,
            min_samples=10,
        )
        for _ in range(12):
            engine.record_sample(window_id="w-4", success=True, latency_ms=45.0, score_delta=0.15)

        view = engine.inspect_window(window_id="w-4", mock_days_observed=7)
        assert view is not None
        assert view["state"] == "graduated"
        assert view["sample_count"] == 12
        assert view["success_rate"] == 1.0
        assert "应验转正" in view["observation_summary"]
        assert '<memory_data id="cehou-w-4"' in view["fenced_summary"]
