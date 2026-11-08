"""测候影子观察视窗落库冒烟测试（v0.68 票 60）。

病灶：`lantai/cognition/cehou.py` 的 `_SHADOW_WINDOWS` 是模块级内存 dict——
1. `create_observation_window` 无条件覆盖（:179），window_id 无属主校验，B 用户可用
   同名 window_id 静默覆盖 A 用户正在观察的视窗（对照数据被篡改）；
2. 进程重启所有视窗蒸发，长期实验全丢；多 worker 各持一份，写入不可控。

票面 TDD 四臂（真库直调，不 mock 被测链路）：
1. 属主臂：A 开 w-1 → B 同 window_id 再开 → 修复前静默覆盖（红），修复后拒绝且
   A 的观察数据原样（绿）——「宁 miss 不脏写」；
2. 持久臂：开窗记样 → 模拟重启（模块内存蒸发）→ 全新引擎实例读取，视窗仍在且
   计数一致——修复前内存 dict 蒸发即红；
3. 并发臂：双引擎实例（各自独立会话）交替写读同一 window_id，终态一致；
   如实记录：修复前双实例恰好共享同一 dict，本臂修复前亦绿，定位是「落库后
   多实例语义回归锁」，不是红臂；
4. 回归臂：真实时钟天数路径 + 转正/熔断判定经库读写后语义与既有断言零变化。
"""

from __future__ import annotations

import uuid

import lantai.cognition.cehou as cehou_mod
from lantai.cognition.cehou import CehouEngine
from lantai.core.auth import Principal


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _evaporate_process_memory() -> None:
    """模拟进程重启：模块级内存态全部蒸发。

    修复前影子视窗的全部状态就是 `_SHADOW_WINDOWS` 这一个模块级 dict，重启等效于
    清空它；落库后该属性不复存在（`getattr` 兜底仅为让清理动作在两种实现下都可
    执行——它不是断言，红/绿判定全部由其后的读取断言承担）。
    """
    mem = getattr(cehou_mod, "_SHADOW_WINDOWS", None)
    if mem is not None:
        mem.clear()


class TestCehouWindowPersistenceSmoke:
    def test_owner_arm_same_window_id_second_user_rejected_not_silent_overwrite(self):
        """属主臂：同名 window_id 第二人再开必须被拒，首个用户的观察数据不得被覆盖。"""
        tag = _uid()
        p_a = Principal(user_id=f"user-a-{tag}", tenant_id=f"tenant-{tag}")
        p_b = Principal(user_id=f"user-b-{tag}", tenant_id=f"tenant-{tag}")
        engine_a = CehouEngine(principal=p_a)
        engine_b = CehouEngine(principal=p_b)
        wid = f"win-owner-{tag}"

        created = engine_a.create_observation_window(
            window_id=wid, target_type="param", target_id=f"sug-{tag}"
        )
        assert created["window_id"] == wid
        # A 先积累观察数据：这些样本属于 A 的对照实验，不允许被 B 抹掉
        for _ in range(3):
            assert engine_a.record_sample(window_id=wid, success=True, latency_ms=10.0)["success"]

        # B 用同一个 window_id 再开：修复前静默覆盖（计数清零、属主易主）
        res_b = engine_b.create_observation_window(
            window_id=wid, target_type="param", target_id=f"sug-{tag}"
        )
        assert res_b.get("success") is False, f"B 用户同名 window_id 再开未被拒绝：{res_b}"
        assert res_b.get("state") == "access_denied"

        # A 的视窗数据原样：属主未易主、样本数未被清零
        seen = engine_a.inspect_window(window_id=wid)
        assert seen is not None and seen.get("state") != "access_denied"
        assert seen["sample_count"] == 3, f"A 的观察样本被 B 的重开清零：{seen}"

        # 本人重开同名窗也不许重置已有观察数据：熔断证据是实验结论，幂等返回现有行
        wid_rb = f"win-owner-rb-{tag}"
        engine_a.create_observation_window(
            window_id=wid_rb, target_type="param", target_id=f"sug-{tag}", required_days=2
        )
        for _ in range(6):
            engine_a.record_sample(window_id=wid_rb, success=False)
        assert engine_a.inspect_window(window_id=wid_rb, mock_days_observed=1)["state"] == (
            "rolled_back"
        )
        again = engine_a.create_observation_window(
            window_id=wid_rb, target_type="param", target_id=f"sug-{tag}", required_days=2
        )
        assert again.get("state") == "rolled_back", f"本人重开把熔断证据重置了：{again}"

    def test_persistence_arm_window_survives_restart_memory_evaporation(self):
        """持久臂：开窗记样后模拟重启（内存蒸发），全新引擎实例仍读到一致数据。"""
        tag = _uid()
        p = Principal(user_id=f"user-p-{tag}", tenant_id=f"tenant-{tag}")
        engine = CehouEngine(principal=p)
        wid = f"win-persist-{tag}"

        engine.create_observation_window(
            window_id=wid, target_type="rule", target_id=f"rule-{tag}", required_days=3
        )
        engine.record_sample(window_id=wid, success=True, latency_ms=5.0)
        engine.record_sample(window_id=wid, success=False, latency_ms=7.0)

        _evaporate_process_memory()

        after = CehouEngine(principal=p)
        res = after.inspect_window(window_id=wid)
        assert res is not None, f"重启后视窗蒸发（内存态未落库）：{wid}"
        assert res["state"] == "observing"
        assert res["sample_count"] == 2
        assert res["target_type"] == "rule"
        assert res["required_days"] == 3

    def test_concurrent_arm_two_engine_instances_serial_readwrite_converge(self):
        """并发臂：双引擎实例（各自独立会话）交替写读同一 window_id，终态一致。"""
        tag = _uid()
        p = Principal(user_id=f"user-c-{tag}", tenant_id=f"tenant-{tag}")
        engine_1 = CehouEngine(principal=p)
        engine_2 = CehouEngine(principal=p)
        wid = f"win-conc-{tag}"

        engine_1.create_observation_window(
            window_id=wid, target_type="proposal", target_id=f"prop-{tag}"
        )
        # 双实例交替记样（各自独立会话），写入不许互相顶掉
        for i in range(4):
            engine = engine_1 if i % 2 == 0 else engine_2
            out = engine.record_sample(window_id=wid, success=(i != 0))
            assert out["success"] is True, f"第 {i} 笔样本写入失败：{out}"

        res = engine_2.inspect_window(window_id=wid)
        assert res is not None and res["sample_count"] == 4
        # success_count/failure_count 不是 to_dict 契约面，用派生的胜率验证记样去向
        assert res["success_rate"] == 0.75

    def test_regression_arm_real_clock_days_and_graduation_through_storage(self):
        """回归臂：真实时钟天数路径不炸，转正/熔断判定经库读写后语义不变。"""
        tag = _uid()
        p = Principal(user_id=f"user-r-{tag}", tenant_id=f"tenant-{tag}")
        engine = CehouEngine(principal=p)
        wid = f"win-regress-{tag}"

        engine.create_observation_window(
            window_id=wid,
            target_type="param",
            target_id=f"sug-{tag}",
            required_days=2,
            min_samples=6,
        )
        # 真实时钟天数路径（不带 mock_days_observed）：刚开的窗 delta≈0，保持 observing
        res_real = engine.inspect_window(window_id=wid)
        assert res_real is not None and res_real["state"] == "observing"
        assert res_real["days_observed"] >= 0

        for _ in range(6):
            engine.record_sample(window_id=wid, success=True, latency_ms=20.0)
        res_grad = engine.inspect_window(window_id=wid, mock_days_observed=2)
        assert res_grad["state"] == "graduated"
        assert res_grad["success_rate"] == 1.0

        # 熔断臂：另一个窗高失败率
        wid_rb = f"win-regress-rb-{tag}"
        engine.create_observation_window(
            window_id=wid_rb, target_type="param", target_id=f"sug-{tag}", required_days=2
        )
        for _ in range(6):
            engine.record_sample(window_id=wid_rb, success=False)
        res_rb = engine.inspect_window(window_id=wid_rb, mock_days_observed=1)
        assert res_rb["state"] == "rolled_back"

    def test_scan_arm_reports_persisted_windows(self):
        """扫描臂：scan_active_windows 报告落库视窗（含属主隔离语义不变）。"""
        tag = _uid()
        p = Principal(user_id=f"user-s-{tag}", tenant_id=f"tenant-{tag}")
        engine = CehouEngine(principal=p)
        wid = f"win-scan-{tag}"
        engine.create_observation_window(window_id=wid, target_type="param", target_id=f"sug-{tag}")
        report = engine.scan_active_windows()
        ids = [w["window_id"] for w in report["windows"]]
        assert wid in ids
        other = Principal(user_id=f"user-s2-{tag}", tenant_id=f"tenant-other-{tag}")
        report_other = CehouEngine(principal=other).scan_active_windows()
        assert wid not in [w["window_id"] for w in report_other["windows"]]
