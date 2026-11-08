"""测候（Cehou）影子观察期巡检与演化状态监控引擎 (ADR-0073)。

出处：沈括《梦溪笔谈》「司天监测候天象，待其应验，以定历法。」
职责：
1. 影子观察视窗（ShadowObservation）生命周期管理（observing / graduated / rolled_back）；
2. 运行样本与实验数据度量统计（胜率、样本量、时延、评分增量）；
3. 判定应验转正或异常熔断回滚；
4. 全链路贯穿樊篱（wrap_as_data）防注入标记隔离；
5. 落实行级租户与属主安全隔离（宁 miss 不脏写）。

存储语义（v0.68 票 60）：影子视窗落 `cehou_window` 表（window_id 作主键，upsert 且
属主校验）。此前是模块级 `_SHADOW_WINDOWS` 内存 dict——多 worker 各进程各持一份、
重启即蒸发、创建路径无属主校验可被同名覆盖夺权，三者随落库一并收口。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
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

    @contextmanager
    def _own_session(self) -> Iterator[Session]:
        """落库会话：装配传入的 session 优先（service 层）；未装配（引擎直调/
        工具冒烟）时自开临时会话。写操作由各方法自行 commit，两种装配下语义一致。"""
        if self.session is not None:
            yield self.session
        else:
            with db.get_session() as session:
                yield session

    def _get_window(self, session: Session, window_id: str) -> CehouWindow | None:
        return session.exec(select(CehouWindow).where(CehouWindow.window_id == window_id)).first()

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
        """创建或注册一个影子观察期视窗（落库）。

        同名 window_id 已存在时先做属主校验：非本人/可信访问者 → 拒绝并如实返回
        access_denied（修复前是无条件覆盖，B 用户可静默篡改 A 的对照实验数据）；
        本人/可信访问者重复开窗 → 幂等返回现有行——窗内样本与状态是实验证据，
        不许被重置。多 worker 并发首建同名窗时 INSERT 撞主键（SELECT/INSERT 间
        有竞态窗口）：转译为 rollback 后按同一属主复检入口裁决，不裸抛 IntegrityError。
        """
        viewer = viewer_of(self.principal) if self.principal else None
        current_tenant = getattr(self.principal, "tenant_id", None) if self.principal else None

        with self._own_session() as session:
            existing = self._get_window(session, window_id)
            if existing is None:
                t = utcnow()
                row = CehouWindow(
                    window_id=window_id,
                    target_type=target_type,
                    target_id=target_id,
                    state="observing",
                    required_days=required_days,
                    min_samples=min_samples,
                    days_observed=0,
                    sample_count=0,
                    success_count=0,
                    failure_count=0,
                    total_latency_ms=0.0,
                    total_score_delta=0.0,
                    alerts=[],
                    tenant_id=current_tenant,
                    user_id=viewer,
                    created_at=t,
                    updated_at=t,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    # 竞态窗口内另一 worker 先落了同名窗：回滚后按同一属主复检入口裁决
                    session.rollback()
                    existing = self._get_window(session, window_id)
                    if existing is None:
                        raise
                else:
                    session.refresh(row)
                    return self._format_row(row).to_dict(fenced=True)
            return self._resolve_existing(session, existing, window_id)

    def _resolve_existing(
        self, session: Session, existing: CehouWindow, window_id: str
    ) -> dict[str, Any]:
        """同名窗已存在时的统一裁决入口：属主校验不过 → 拒绝；通过 → 幂等返回现有行。"""
        if not self._check_access(owner_id=existing.user_id, tenant_id=existing.tenant_id):
            return {
                "success": False,
                "state": "access_denied",
                "message": (
                    f"影子观察视窗 {window_id} 已存在且属主校验未通过，拒绝覆盖（宁 miss 不脏写）"
                ),
            }
        return self._format_row(existing).to_dict(fenced=True)

    def record_sample(
        self,
        window_id: str,
        success: bool,
        latency_ms: float = 0.0,
        score_delta: float = 0.0,
    ) -> dict[str, Any]:
        """向指定视窗上报一条实测运行样本（落库）。"""
        with self._own_session() as session:
            row = self._get_window(session, window_id)
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

            # 漂移与报警检测（JSON 列的内嵌 list 不被 ORM 追踪，必须整列重新赋值）
            s_count = row.sample_count
            fail_rate = row.failure_count / max(s_count, 1)
            if s_count >= 5 and fail_rate >= 0.4:
                alert = f"视窗 {window_id} 错误率激增至 {round(fail_rate * 100, 1)}%"
                if alert not in row.alerts:
                    row.alerts = [*row.alerts, alert]

            session.add(row)
            session.commit()
            return {"success": True, "window_id": window_id, "sample_count": s_count}

    def inspect_window(
        self,
        window_id: str,
        mock_days_observed: int | None = None,
    ) -> dict[str, Any] | None:
        """巡检指定观察期视窗，核算累计指标并裁决状态跃迁（落库）。"""
        with self._own_session() as session:
            row = self._get_window(session, window_id)
            if row is None:
                return None

            if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                return {"state": "access_denied", "message": "无权访问"}

            obs = self._inspect_row(session, row, mock_days_observed)
            session.commit()
            return obs.to_dict(fenced=True)

    def scan_active_windows(self, target_type: str | None = None) -> dict[str, Any]:
        """全域扫描当前租户内影子视窗并生成汇总巡检报告（落库）。"""
        windows: list[dict[str, Any]] = []
        alerts: list[str] = []
        obs_c = 0
        grad_c = 0
        rb_c = 0

        with self._own_session() as session:
            rows = session.exec(select(CehouWindow)).all()
            for row in rows:
                if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                    continue
                if target_type and row.target_type != target_type:
                    continue

                obs = self._inspect_row(session, row, None)
                res = obs.to_dict(fenced=True)

                state = res["state"]
                if state == "observing":
                    obs_c += 1
                elif state == "graduated":
                    grad_c += 1
                elif state == "rolled_back":
                    rb_c += 1

                for a in row.alerts or []:
                    if a not in alerts:
                        alerts.append(a)

                windows.append(res)
            session.commit()

        report = InspectionReport(
            total_windows=len(windows),
            observing_count=obs_c,
            graduated_count=grad_c,
            rolled_back_count=rb_c,
            alerts=alerts,
            windows=windows,
        )
        return report.to_dict(fenced=True)

    def _inspect_row(
        self,
        session: Session,
        row: CehouWindow,
        mock_days_observed: int | None,
    ) -> ShadowObservation:
        """就地核算单行指标并裁决状态跃迁（写回字段，commit 由调用方负责）。

        与内存形态的判定语义逐条一致：真实时钟天数 = (utcnow - created_at).days
        下钳 0；熔断 = 样本数 ≥ min(min_samples, 5) 且失败率 ≥ 0.6；
        转正 = 天数与样本均达标且胜率 ≥ 0.8。
        """
        if mock_days_observed is not None:
            row.days_observed = mock_days_observed
        else:
            # sqlmodel 0.0.42 无库级时区防护，SQLite 读回的 created_at 是 naive——
            # 按「naive 即 UTC」的仓口径补齐后再相减（core.time.ensure_aware 约定）
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
        session.add(row)
        return self._format_row(row)

    def _format_row(self, row: CehouWindow) -> ShadowObservation:
        s_count = row.sample_count
        succ_rate = row.success_count / max(s_count, 1) if s_count > 0 else 0.0
        avg_lat = row.total_latency_ms / max(s_count, 1) if s_count > 0 else 0.0
        avg_score_d = row.total_score_delta / max(s_count, 1) if s_count > 0 else 0.0

        state = row.state
        if state == "graduated":
            summary = (
                f"视窗 {row.window_id} ({row.target_type}:{row.target_id}) "
                f"已观察 {row.days_observed}/{row.required_days} 天，"
                f"累计 {s_count} 样本且胜率达 {round(succ_rate * 100, 1)}%，已应验转正入库"
            )
        elif state == "rolled_back":
            summary = (
                f"视窗 {row.window_id} 胜率仅 {round(succ_rate * 100, 1)}%，"
                "发生显著指标退化，已异常回滚"
            )
        else:
            summary = (
                f"视窗 {row.window_id} 观测中 ({row.days_observed}/{row.required_days} 天)，"
                f"样本数: {s_count}/{row.min_samples}，胜率: {round(succ_rate * 100, 1)}%"
            )

        return ShadowObservation(
            window_id=row.window_id,
            target_type=row.target_type,
            target_id=row.target_id,
            state=state,
            days_observed=row.days_observed,
            required_days=row.required_days,
            sample_count=s_count,
            min_samples=row.min_samples,
            success_rate=succ_rate,
            metrics_delta={
                "avg_latency_ms": round(avg_lat, 2),
                "avg_score_delta": round(avg_score_d, 4),
            },
            observation_summary=summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
        )
