"""测候（Cehou）核心影子观察期巡检与演化状态监控引擎冒烟测试 (Ticket 01)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测核心引擎，不 mock 存储与指标统计；
- 覆盖 4 个公共接缝：
  1. 影子视窗指标核算与应验转正判定（inspect_window）；
  2. 活跃视窗巡检与风险报警（scan_active_windows）；
  3. 樊篱 wrap_as_data 隔离标记防护；
  4. 租户与属主行级 ACL 隔离（跨租户观察视窗禁止读取与记样）。
"""

from __future__ import annotations

import pytest

from lantai.cognition.cehou import CehouEngine, InspectionReport, ShadowObservation
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import ParamSuggestion
from lantai.storage import db


def _seed_param_suggestion(
    suggestion_id: str = "sug-cehou-001",
    status: str = "pending",
    confidence: float = 0.9,
) -> ParamSuggestion:
    t = utcnow()
    with db.get_session() as s:
        sug = ParamSuggestion(
            id=suggestion_id,
            run_id="run-cehou-01",
            status=status,
            confidence=confidence,
            title="提升 FTS trigram 权重",
            summary="提高 BM25 与 FTS 在代码搜索时的召回率",
            rationale="提升检索精确度",
            expected_benefit="降低零召回率 15%",
            risk_notes="轻微增加计算延迟",
            validation_plan="观察 7 天胜率",
            base_snapshot_hash="hash-cehou-01",
            fingerprint=f"fp-{suggestion_id}-{t.timestamp()}",
            created_at=t,
        )
        s.add(sug)
        s.commit()
        s.refresh(sug)
        return sug


class TestCehouEngineSmoke:
    """Cehou 核心引擎 4 大接缝冒烟测试集。"""

    def test_seam1_inspect_window_graduation_and_rollback(self):
        """Seam 1: 影子视窗指标核算与应验转正判定（inspect_window）。"""
        sug = _seed_param_suggestion(suggestion_id="sug-cehou-seam1")
        p = Principal(user_id="user-cehou-seam1", tenant_id="tenant-cehou-a")
        engine = CehouEngine(principal=p)

        # 1. 创建并上报样本至观察期视窗
        w_id = f"win-cehou-{sug.id}"
        engine.create_observation_window(
            window_id=w_id,
            target_type="param",
            target_id=sug.id,
            required_days=7,
            min_samples=10,
        )

        # 初始检查：尚未达到天数与样本，保持 observing
        res_init = engine.inspect_window(window_id=w_id)
        assert res_init["window_id"] == w_id
        assert res_init["state"] == "observing"
        assert res_init["sample_count"] == 0

        # 上报高胜率样本满足转正条件 (12 次成功，0 次失败)
        for _ in range(12):
            engine.record_sample(window_id=w_id, success=True, latency_ms=45.0, score_delta=0.15)

        # 模拟观察天数达标并检查
        res_grad = engine.inspect_window(window_id=w_id, mock_days_observed=7)
        assert res_grad["state"] == "graduated"
        assert res_grad["sample_count"] == 12
        assert res_grad["success_rate"] == 1.0
        assert "应验转正" in res_grad["observation_summary"]

        # 2. 模拟高失败率触发熔断回滚
        w_fail = f"win-fail-{sug.id}"
        engine.create_observation_window(
            window_id=w_fail,
            target_type="param",
            target_id=sug.id,
            required_days=7,
            min_samples=10,
        )
        for _ in range(10):
            engine.record_sample(
                window_id=w_fail, success=False, latency_ms=120.0, score_delta=-0.3
            )

        res_rb = engine.inspect_window(window_id=w_fail, mock_days_observed=2)
        assert res_rb["state"] == "rolled_back"
        assert res_rb["success_rate"] == 0.0
        assert (
            "异常回滚" in res_rb["observation_summary"] or "熔断" in res_rb["observation_summary"]
        )

    def test_seam2_scan_active_windows_alerts(self):
        """Seam 2: 活跃视窗巡检与风险报警（scan_active_windows）。"""
        sug = _seed_param_suggestion(suggestion_id="sug-cehou-seam2")
        p = Principal(user_id="user-cehou-seam2", tenant_id="tenant-cehou-a")
        engine = CehouEngine(principal=p)

        w1 = f"win-scan-1-{sug.id}"
        w2 = f"win-scan-2-{sug.id}"
        engine.create_observation_window(window_id=w1, target_type="param", target_id=sug.id)
        engine.create_observation_window(window_id=w2, target_type="rule", target_id="rule-99")

        # 注入一个异常点
        engine.record_sample(window_id=w1, success=False, latency_ms=500.0)

        report = engine.scan_active_windows()
        assert report["total_windows"] >= 2
        assert report["observing_count"] >= 2
        assert isinstance(report["alerts"], list)

    def test_seam3_fencing_protection(self):
        """Seam 3: 樊篱 wrap_as_data 隔离标记防护。"""
        sug = _seed_param_suggestion(suggestion_id="sug-cehou-seam3")
        p = Principal(user_id="user-cehou-seam3", tenant_id="tenant-cehou-a")
        engine = CehouEngine(principal=p)

        w_id = f"win-fencing-{sug.id}"
        engine.create_observation_window(window_id=w_id, target_type="param", target_id=sug.id)
        engine.record_sample(window_id=w_id, success=True, latency_ms=50.0)

        inspect_res = engine.inspect_window(window_id=w_id)
        assert "fenced_summary" in inspect_res
        assert f'<memory_data id="cehou-{w_id}"' in inspect_res["fenced_summary"]
        assert "</memory_data>" in inspect_res["fenced_summary"]

        report = engine.scan_active_windows()
        assert "fenced_report" in report
        assert '<memory_data id="' in report["fenced_report"]
        assert "</memory_data>" in report["fenced_report"]

    def test_seam4_row_level_acl_isolation(self):
        """Seam 4: 租户与属主行级 ACL 隔离（跨租户观察视窗禁止读取与记样）。"""
        sug = _seed_param_suggestion(suggestion_id="sug-cehou-seam4")
        p_alpha = Principal(user_id="user-alpha", tenant_id="tenant-alpha")
        engine_alpha = CehouEngine(principal=p_alpha)

        w_alpha = f"win-tenant-alpha-{sug.id}"
        engine_alpha.create_observation_window(
            window_id=w_alpha,
            target_type="param",
            target_id=sug.id,
        )

        # 租户 Beta 访问
        p_beta = Principal(user_id="user-beta", tenant_id="tenant-beta")
        engine_beta = CehouEngine(principal=p_beta)

        # 1. 尝试巡检 Alpha 的视窗 -> 拒绝访问或返回 None
        res = engine_beta.inspect_window(window_id=w_alpha)
        assert res is None or res.get("state") == "access_denied"

        # 2. 尝试向 Alpha 的视窗注入样本 -> 阻断
        rec_res = engine_beta.record_sample(window_id=w_alpha, success=True)
        assert rec_res.get("success") is False
        assert "无权访问" in rec_res.get("message", "")
