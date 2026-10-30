"""测候（Cehou）影子观察期巡检与演化状态监控引擎 (ADR-0073)。

出处：沈括《梦溪笔谈》「司天监测候天象，待其应验，以定历法。」
职责：
1. 影子观察视窗（ShadowObservation）生命周期管理（observing / graduated / rolled_back）；
2. 运行样本与实验数据度量统计（胜率、样本量、时延、评分增量）；
3. 判定应验转正或异常熔断回滚；
4. 全链路贯穿樊篱（wrap_as_data）防注入标记隔离；
5. 落实行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.storage import db

# 内存/进程级全局视窗注册表（持久化支持结合 SQLite/DB，提供轻量高频度量通道）
_SHADOW_WINDOWS: dict[str, dict[str, Any]] = {}


@dataclass
class ShadowObservation:
    """影子观察视窗契约。"""

    window_id: str
    target_type: str  # param | rule | proposal
    target_id: str
    state: str  # observing | graduated | rolled_back
    days_observed: int
    required_days: int
    sample_count: int
    min_samples: int
    success_rate: float
    metrics_delta: dict[str, float]
    observation_summary: str
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    user_id: str | None = None
    tenant_id: str | None = None

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        fenced_s = (
            wrap_as_data(
                self.observation_summary,
                item_id=f"cehou-{self.window_id}",
                score=self.success_rate,
            )
            if fenced
            else self.observation_summary
        )
        return {
            "window_id": self.window_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "state": self.state,
            "days_observed": self.days_observed,
            "required_days": self.required_days,
            "sample_count": self.sample_count,
            "min_samples": self.min_samples,
            "success_rate": round(self.success_rate, 4),
            "metrics_delta": self.metrics_delta,
            "observation_summary": self.observation_summary,
            "fenced_summary": fenced_s,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
        }


@dataclass
class InspectionReport:
    """测候巡检汇总报告契约。"""

    total_windows: int
    observing_count: int
    graduated_count: int
    rolled_back_count: int
    alerts: list[str]
    windows: list[dict[str, Any]]
    inspected_at: datetime = field(default_factory=utcnow)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        summary = (
            f"测候共巡检 {self.total_windows} 个视窗：观察中 {self.observing_count}，"
            f"已转正 {self.graduated_count}，已回滚 {self.rolled_back_count}；"
            f"报警数: {len(self.alerts)}"
        )
        fenced_r = (
            wrap_as_data(
                summary,
                item_id=f"cehou-report-{int(self.inspected_at.timestamp())}",
                score=0.9,
            )
            if fenced
            else summary
        )
        return {
            "total_windows": self.total_windows,
            "observing_count": self.observing_count,
            "graduated_count": self.graduated_count,
            "rolled_back_count": self.rolled_back_count,
            "alerts": self.alerts,
            "windows": self.windows,
            "report_summary": summary,
            "fenced_report": fenced_r,
            "inspected_at": self.inspected_at.isoformat(),
        }


class CehouEngine:
    """测候（Cehou）影子观察期巡检与演化状态监控引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """行级安全隔离校验（宁 miss 不脏写）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    def create_observation_window(
        self,
        window_id: str,
        target_type: str,
        target_id: str,
        required_days: int = 7,
        min_samples: int = 10,
    ) -> dict[str, Any]:
        """创建或注册一个影子观察期视窗。"""
        viewer = viewer_of(self.principal) if self.principal else None
        current_tenant = getattr(self.principal, "tenant_id", None) if self.principal else None

        t = utcnow()
        data = {
            "window_id": window_id,
            "target_type": target_type,
            "target_id": target_id,
            "state": "observing",
            "required_days": required_days,
            "min_samples": min_samples,
            "days_observed": 0,
            "sample_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_latency_ms": 0.0,
            "total_score_delta": 0.0,
            "alerts": [],
            "created_at": t,
            "updated_at": t,
            "user_id": viewer,
            "tenant_id": current_tenant,
        }
        _SHADOW_WINDOWS[window_id] = data
        return self._format_observation(data).to_dict(fenced=True)

    def record_sample(
        self,
        window_id: str,
        success: bool,
        latency_ms: float = 0.0,
        score_delta: float = 0.0,
    ) -> dict[str, Any]:
        """向指定视窗上报一条实测运行样本。"""
        data = _SHADOW_WINDOWS.get(window_id)
        if not data:
            return {"success": False, "message": f"Window {window_id} not found"}

        if not self._check_access(owner_id=data.get("user_id"), tenant_id=data.get("tenant_id")):
            return {"success": False, "message": "无权访问此影子观察视窗"}

        data["sample_count"] += 1
        if success:
            data["success_count"] += 1
        else:
            data["failure_count"] += 1

        data["total_latency_ms"] += latency_ms
        data["total_score_delta"] += score_delta
        data["updated_at"] = utcnow()

        # 漂移与报警检测
        s_count = data["sample_count"]
        fail_rate = data["failure_count"] / max(s_count, 1)
        if s_count >= 5 and fail_rate >= 0.4:
            alert = f"视窗 {window_id} 错误率激增至 {round(fail_rate * 100, 1)}%"
            if alert not in data["alerts"]:
                data["alerts"].append(alert)

        return {"success": True, "window_id": window_id, "sample_count": s_count}

    def inspect_window(
        self,
        window_id: str,
        mock_days_observed: int | None = None,
    ) -> dict[str, Any] | None:
        """巡检指定观察期视窗，核算累计指标并裁决状态跃迁。"""
        data = _SHADOW_WINDOWS.get(window_id)
        if not data:
            return None

        if not self._check_access(owner_id=data.get("user_id"), tenant_id=data.get("tenant_id")):
            return {"state": "access_denied", "message": "无权访问"}

        if mock_days_observed is not None:
            data["days_observed"] = mock_days_observed
        else:
            delta = (utcnow() - data["created_at"]).days
            data["days_observed"] = max(delta, 0)

        s_count = data["sample_count"]
        succ_rate = data["success_count"] / max(s_count, 1) if s_count > 0 else 0.0
        days = data["days_observed"]
        req_days = data["required_days"]
        min_samp = data["min_samples"]

        # 判定状态跃迁
        if data["state"] == "observing":
            # 1. 熔断回滚：样本不少于 5 且失败率高于 60%
            if s_count >= min(min_samp, 5) and (1.0 - succ_rate) >= 0.6:
                data["state"] = "rolled_back"
                data["alerts"].append("观察期实测指标严重劣化，触发自动熔断回滚")
            # 2. 应验转正：天数与样本均满足且胜率 ≥ 80%
            elif days >= req_days and s_count >= min_samp and succ_rate >= 0.8:
                data["state"] = "graduated"

        data["updated_at"] = utcnow()
        obs = self._format_observation(data)
        return obs.to_dict(fenced=True)

    def scan_active_windows(self, target_type: str | None = None) -> dict[str, Any]:
        """全域扫描当前租户内影子视窗并生成汇总巡检报告。"""
        windows: list[dict[str, Any]] = []
        alerts: list[str] = []
        obs_c = 0
        grad_c = 0
        rb_c = 0

        for w_id in list(_SHADOW_WINDOWS.keys()):
            res = self.inspect_window(w_id)
            if not res or res.get("state") == "access_denied":
                continue
            if target_type and res.get("target_type") != target_type:
                continue

            state = res["state"]
            if state == "observing":
                obs_c += 1
            elif state == "graduated":
                grad_c += 1
            elif state == "rolled_back":
                rb_c += 1

            raw = _SHADOW_WINDOWS[w_id]
            for a in raw.get("alerts", []):
                if a not in alerts:
                    alerts.append(a)

            windows.append(res)

        report = InspectionReport(
            total_windows=len(windows),
            observing_count=obs_c,
            graduated_count=grad_c,
            rolled_back_count=rb_c,
            alerts=alerts,
            windows=windows,
        )
        return report.to_dict(fenced=True)

    def _format_observation(self, data: dict[str, Any]) -> ShadowObservation:
        s_count = data["sample_count"]
        succ_rate = data["success_count"] / max(s_count, 1) if s_count > 0 else 0.0
        avg_lat = data["total_latency_ms"] / max(s_count, 1) if s_count > 0 else 0.0
        avg_score_d = data["total_score_delta"] / max(s_count, 1) if s_count > 0 else 0.0

        state = data["state"]
        if state == "graduated":
            summary = (
                f"视窗 {data['window_id']} ({data['target_type']}:{data['target_id']}) "
                f"已观察 {data['days_observed']}/{data['required_days']} 天，"
                f"累计 {s_count} 样本且胜率达 {round(succ_rate * 100, 1)}%，已应验转正入库"
            )
        elif state == "rolled_back":
            summary = (
                f"视窗 {data['window_id']} 胜率仅 {round(succ_rate * 100, 1)}%，"
                "发生显著指标退化，已异常回滚"
            )
        else:
            summary = (
                f"视窗 {data['window_id']} 观测中 ({data['days_observed']}/{data['required_days']} 天)，"
                f"样本数: {s_count}/{data['min_samples']}，胜率: {round(succ_rate * 100, 1)}%"
            )

        return ShadowObservation(
            window_id=data["window_id"],
            target_type=data["target_type"],
            target_id=data["target_id"],
            state=state,
            days_observed=data["days_observed"],
            required_days=data["required_days"],
            sample_count=s_count,
            min_samples=data["min_samples"],
            success_rate=succ_rate,
            metrics_delta={
                "avg_latency_ms": round(avg_lat, 2),
                "avg_score_delta": round(avg_score_d, 4),
            },
            observation_summary=summary,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            user_id=data.get("user_id"),
            tenant_id=data.get("tenant_id"),
        )
