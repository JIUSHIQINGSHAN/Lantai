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

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from lantai.core.acl import is_system_viewer, viewer_of
from lantai.core.auth import Principal
from lantai.core.time import ensure_aware, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import CehouWindow
from lantai.storage import db


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
        """创建或注册一个影子观察期视窗（票 60：落库 CehouWindow，window_id 主键）。

        属主校验：已存在且非本人 → HTTPException(409) 拒绝覆盖（宁 miss 不脏写，
        不再静默篡改他人实验对照组）；admin/SYSTEM_VIEWER 豁免；同属主重开 =
        重新注册本 ID 实验（upsert 重置观察计数）。owner 恒 `viewer_of` 收敛
        （None→"default"，不再落 NULL 属主窗，票 57 口径）。
        """
        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)
        t = utcnow()

        # 票 66：先验后写——required_days<=0 时 days>=req_days 恒真，按天观察
        # 闸门形同虚设。界对齐 REST CreateWindowRequest Field [1,90]/[1,1000]
        # （双面同钳，REST 为真源）；拒绝而非静默钳制（宁 miss 不脏写）。
        if (
            isinstance(required_days, bool)
            or not isinstance(required_days, int)
            or not 1 <= required_days <= 90
        ):
            raise ValueError(f"required_days 须为 [1, 90] 内整数，收到 {required_days!r}")
        if (
            isinstance(min_samples, bool)
            or not isinstance(min_samples, int)
            or not 1 <= min_samples <= 1000
        ):
            raise ValueError(f"min_samples 须为 [1, 1000] 内整数，收到 {min_samples!r}")

        def _create(s: Session) -> dict[str, Any]:
            row = s.get(CehouWindow, window_id)
            privileged = getattr(self.principal, "is_admin", False) or is_system_viewer(
                self.principal
            )
            if row is not None and not privileged and row.user_id not in (None, viewer):
                raise HTTPException(
                    status_code=409,
                    detail=f"shadow window {window_id} already exists with a different owner",
                )
            # 同 ID = 原行更新（PK 不动，重新注册本 ID 实验）；新 ID = 建行
            data = row if row is not None else CehouWindow(window_id=window_id)
            data.target_type = target_type
            data.target_id = target_id
            data.state = "observing"
            data.required_days = required_days
            data.min_samples = min_samples
            data.days_observed = 0
            data.sample_count = 0
            data.success_count = 0
            data.failure_count = 0
            data.total_latency_ms = 0.0
            data.total_score_delta = 0.0
            data.alerts = []
            data.created_at = t
            data.updated_at = t
            data.user_id = viewer
            data.tenant_id = current_tenant
            s.add(data)
            s.commit()
            s.refresh(data)  # expire_on_commit 清空 __dict__，refresh 后 model_dump 才有值
            return self._format_observation(data.model_dump()).to_dict(fenced=True)

        if self.session is not None:
            return _create(self.session)
        with db.get_session() as s:
            return _create(s)

    def record_sample(
        self,
        window_id: str,
        success: bool,
        latency_ms: float = 0.0,
        score_delta: float = 0.0,
    ) -> dict[str, Any]:
        """向指定视窗上报一条实测运行样本。"""

        # 票 66：先验后写——非有限值落 REAL 累计列是永久毒化（NaN 绑定 NULL、
        # 比较恒假；inf 累计后任何后续裁决都失真），负耗时是无意义样本。
        # 引擎单咽喉点同守 MCP 裸传面与 REST 面；拒绝而非静默钳制。
        for _nm, _val in (("latency_ms", latency_ms), ("score_delta", score_delta)):
            if (
                isinstance(_val, bool)
                or not isinstance(_val, (int, float))
                or not math.isfinite(_val)
            ):
                raise ValueError(f"{_nm} 须为有限数值，收到 {_val!r}")
        if latency_ms < 0:
            raise ValueError(f"latency_ms 不得为负，收到 {latency_ms!r}")

        def _record(s: Session) -> dict[str, Any]:
            row = s.get(CehouWindow, window_id)
            if row is None:
                return {"success": False, "message": f"Window {window_id} not found"}

            if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                return {"success": False, "message": "无权访问此影子观察视窗"}

            row.sample_count += 1
            if success:
                row.success_count += 1
            else:
                row.failure_count += 1

            row.total_latency_ms += latency_ms
            row.total_score_delta += score_delta
            row.updated_at = utcnow()

            # 漂移与报警检测（JSON 列原地 append 不被 SQLAlchemy 判脏，整体重赋值）
            s_count = row.sample_count
            fail_rate = row.failure_count / max(s_count, 1)
            if s_count >= 5 and fail_rate >= 0.4:
                alert = f"视窗 {window_id} 错误率激增至 {round(fail_rate * 100, 1)}%"
                if alert not in row.alerts:
                    row.alerts = [*row.alerts, alert]

            s.add(row)
            s.commit()
            return {"success": True, "window_id": window_id, "sample_count": s_count}

        if self.session is not None:
            return _record(self.session)
        with db.get_session() as s:
            return _record(s)

    def inspect_window(
        self,
        window_id: str,
        mock_days_observed: int | None = None,
    ) -> dict[str, Any] | None:
        """巡检指定观察期视窗，核算累计指标并裁决状态跃迁。"""

        # 票 66：先验后写——str mock_days 落库后 days>=req_days TypeError 毒化
        # 该窗所有后续巡检（票 60 只救了事务，类型脏值仍可入库）；10**9 一检
        # 即伪造毕业。界 [0, 36500]（百年）对齐 REST InspectRequest。
        if mock_days_observed is not None:
            if (
                isinstance(mock_days_observed, bool)
                or not isinstance(mock_days_observed, int)
                or not 0 <= mock_days_observed <= 36500
            ):
                raise ValueError(
                    f"mock_days_observed 须为 [0, 36500] 内整数，收到 {mock_days_observed!r}"
                )

        def _inspect(s: Session) -> dict[str, Any] | None:
            row = s.get(CehouWindow, window_id)
            if row is None:
                return None

            if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                return {"state": "access_denied", "message": "无权访问"}

            if mock_days_observed is not None:
                row.days_observed = mock_days_observed
            else:
                # SQLite 往返剥 tzinfo（票 32 归一口径）：绑定前必 ensure_aware
                row.days_observed = max((utcnow() - ensure_aware(row.created_at)).days, 0)

            s_count = row.sample_count
            succ_rate = row.success_count / max(s_count, 1) if s_count > 0 else 0.0
            days = row.days_observed
            req_days = row.required_days
            min_samp = row.min_samples

            # 判定状态跃迁
            if row.state == "observing":
                # 1. 熔断回滚：样本不少于 5 且失败率高于 60%
                if s_count >= min(min_samp, 5) and (1.0 - succ_rate) >= 0.6:
                    row.state = "rolled_back"
                    row.alerts = [*row.alerts, "观察期实测指标严重劣化，触发自动熔断回滚"]
                # 2. 应验转正：天数与样本均满足且胜率 ≥ 80%
                elif days >= req_days and s_count >= min_samp and succ_rate >= 0.8:
                    row.state = "graduated"

            row.updated_at = utcnow()
            s.add(row)
            s.commit()
            s.refresh(row)  # 同 create：expire_on_commit 后需 refresh 再 model_dump
            return self._format_observation(row.model_dump()).to_dict(fenced=True)

        if self.session is not None:
            return _inspect(self.session)
        with db.get_session() as s:
            return _inspect(s)

    def scan_active_windows(self, target_type: str | None = None) -> dict[str, Any]:
        """全域扫描当前权限范围内的影子视窗并生成汇总巡检报告。

        行级 ACL 下推 SQL（票 57 口径）：None 收敛 `viewer_of`="default"（只见默认
        属主 + NULL 老行窗），admin/`SYSTEM_VIEWER` 全表；租户不匹配同判不可见。
        """
        windows: list[dict[str, Any]] = []
        alerts: list[str] = []
        obs_c = 0
        grad_c = 0
        rb_c = 0

        def _scan(s: Session) -> dict[str, Any]:
            nonlocal obs_c, grad_c, rb_c
            stmt = select(CehouWindow)
            if not (getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal)):
                viewer = viewer_of(self.principal)
                current_tenant = getattr(self.principal, "tenant_id", None)
                owner_clause = (CehouWindow.user_id == viewer) | (CehouWindow.user_id.is_(None))
                if current_tenant:
                    owner_clause = owner_clause & (
                        (CehouWindow.tenant_id.is_(None))
                        | (CehouWindow.tenant_id == current_tenant)
                    )
                stmt = stmt.where(owner_clause)
            rows = list(s.exec(stmt).all())

            for row in rows:
                res = self.inspect_window(row.window_id)
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

                for a in row.alerts:
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

        if self.session is not None:
            return _scan(self.session)
        with db.get_session() as s:
            return _scan(s)

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
