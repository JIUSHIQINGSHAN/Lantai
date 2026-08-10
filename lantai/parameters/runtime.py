"""
运行时参数层——DB 是事实源，settings 单例原位刷新。

关键约束（ADR-0001 门面铁律的延伸）：
- 保持 id(settings) 不变，只对六个白名单属性 setattr，旧 import 全绿。
- 启动时加载 DB head；跨进程用 revision 轮询（PARAM_OVERRIDE_REFRESH_SECONDS）。
- 损坏的 DB 快照不应用（校验失败即拒绝，保持上一有效配置）。
"""
from sqlmodel import select, func

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import ParamOverride
from lantai.parameters.registry import (
    default_snapshot,
    get_registry_version,
)
from lantai.parameters.validation import (
    ParamValidationError,
    validate_snapshot,
)
from lantai.storage import db

# 每个进程各自记住"已应用到的 revision"，跨进程不共享（DB 才是事实源）
_last_applied_revision: int = -1


def _head_override(session) -> ParamOverride | None:
    return session.exec(
        select(ParamOverride).order_by(ParamOverride.revision.desc())
    ).first()


def get_effective_param_snapshot(session) -> dict:
    """当前有效快照：head override 的 after_snapshot；无 override 时用静态默认。"""
    head = _head_override(session)
    if head is None:
        return default_snapshot()
    # 启动/刷新加载时校验——损坏则拒绝应用，保持上一有效配置
    try:
        return validate_snapshot(head.after_snapshot)
    except ParamValidationError as e:
        logger.error("DB head snapshot invalid, refusing to apply: %s", e)
        raise


def get_effective_state(session) -> dict:
    """返回 (snapshot, revision, hash, registry_version) 的完整状态。"""
    head = _head_override(session)
    if head is None:
        snap = default_snapshot()
        return {"snapshot": snap, "revision": 0,
                "snapshot_hash": _hash(snap),
                "registry_version": get_registry_version()}
    return {"snapshot": head.after_snapshot, "revision": head.revision,
            "snapshot_hash": head.after_snapshot_hash,
            "registry_version": head.registry_version}


def _hash(snapshot: dict) -> str:
    from lantai.parameters.validation import snapshot_hash
    return snapshot_hash(snapshot)


def apply_snapshot_to_settings(snapshot: dict) -> bool:
    """
    将快照原位写入 settings 单例（白名单参数，先校验）。
    返回是否有实际变化。
    """
    from lantai.parameters.registry import get_param_registry
    validated = validate_snapshot(snapshot, get_param_registry(),
                                  allow_partial=True)
    changed = False
    for name, value in validated.items():
        if getattr(settings, name) != value:
            setattr(settings, name, value)
            changed = True
    return changed


def refresh_runtime_params() -> dict:
    """
    周期刷新：读取 DB head revision，变化则原位应用。
    供 scheduler 每 PARAM_OVERRIDE_REFRESH_SECONDS 调用一次。
    """
    global _last_applied_revision
    with db.get_session() as s:
        head = _head_override(s)
        if head is None:
            revision, snapshot = 0, default_snapshot()
        else:
            revision, snapshot = head.revision, head.after_snapshot
    applied = False
    if revision != _last_applied_revision:
        try:
            applied = apply_snapshot_to_settings(snapshot)
            _last_applied_revision = revision
            logger.info("runtime params refreshed: revision=%d applied=%s",
                        revision, applied)
        except ParamValidationError as e:
            logger.error("runtime refresh rejected (keep previous config): %s", e)
            _last_applied_revision = revision  # 防刷屏，保持上一配置
    return {"revision": revision, "applied": applied}


def load_runtime_params_at_startup() -> None:
    """启动时强制加载 DB head（lifespan 调用）。"""
    global _last_applied_revision
    with db.get_session() as s:
        head = _head_override(s)
        if head is None:
            return
        snapshot = validate_snapshot(head.after_snapshot)
    applied = apply_snapshot_to_settings(snapshot)
    _last_applied_revision = head.revision
    if applied:
        logger.info("startup: loaded param override revision=%d", head.revision)

# ── Step 7 影子观察期 ────────────────────────────────────────────────
# DEDUP shadow-only：影子参数不写 ParamOverride，只存 ShadowWindow 表；
# promote 只标记状态，实际应用走 ParamSuggestion 人工闸门（绝不自动应用）。


def open_shadow(override_id: str, param_overrides: dict, *,
                observe_days: int | None = None):
    """建议批准后打开观察窗（status=observing）。

    影子参数仅记录在本窗，不写入 ParamOverride（DEDUP shadow-only）。
    """
    from datetime import timedelta
    from lantai.core.settings import settings as _s
    from lantai.parameters.trust_models import ShadowWindow

    days = observe_days or _s.SHADOW_OBSERVE_DAYS
    with db.get_session() as s:
        # MAX_ACTIVE_SHADOW_WINDOWS 护栏：超过上限先取消最旧的 observing
        active = s.exec(
            select(ShadowWindow).where(ShadowWindow.status == "observing")
        ).all()
        if len(active) >= _s.MAX_ACTIVE_SHADOW_WINDOWS:
            oldest = min(active, key=lambda w: w.started_at)
            oldest.status = "cancelled"
            oldest.finished_at = utcnow()
            oldest.verdict_reason = "cancelled_by_new_window"
            s.add(oldest)

        window = ShadowWindow(
            id=new_id("sw"),
            override_id=override_id,
            base_revision=0,
            param_overrides=dict(param_overrides),
            base_snapshot=default_snapshot(),
            status="observing",
            check_deadline=utcnow() + timedelta(days=days),
        )
        s.add(window)
        s.commit()
        s.refresh(window)
        return window


def check_shadow_due() -> list:
    """轮询到期观察窗：跑对比 dry-run，调 evaluate_window 判定，更新状态。

    返回每个到期窗的判定结果摘要（供调度/日志/测试）。
    """
    from lantai.core.settings import settings as _s
    from lantai.parameters.shadow import shadow_is_due
    from lantai.parameters.trust_models import ShadowWindow

    results: list = []
    with db.get_session() as s:
        windows = s.exec(select(ShadowWindow).where(
            ShadowWindow.status == "observing")).all()
        due = [w for w in windows if shadow_is_due(w)]

    for w in due:
        summary = _evaluate_one_window(w, auto_rollback=_s.SHADOW_AUTO_ROLLBACK_ENABLED)
        results.append(summary)
    return results


def _evaluate_one_window(window, *, auto_rollback: bool = True) -> dict:
    """单个观察窗：跑基线+影子 dry-run，判定，更新状态。

    promote 只标记（交人工闸门），rollback 恢复 base_snapshot（护栏）。
    """
    from lantai.parameters.shadow import evaluate_window
    from lantai.parameters.trust_models import ShadowWindow

    with db.get_session() as s:
        w = s.get(ShadowWindow, window.id)
        if w is None or w.status != "observing":
            return {"window_id": window.id, "skipped": True}

        base_metrics = _run_shadow_dry_run(s, w, base=True)
        shadow_metrics = _run_shadow_dry_run(s, w, base=False)
        w.metrics_base = base_metrics
        w.metrics_shadow = shadow_metrics

        verdict = evaluate_window(
            base_metrics,
            {**shadow_metrics,
             "jaccard_vs_baseline": shadow_metrics.get("jaccard_vs_baseline")},
        )
        v = verdict["verdict"]
        w.verdict_reason = verdict["reason"]
        w.finished_at = utcnow()

        if v == "rollback":
            w.status = "rolled_back"
            w.rollback_reason = verdict["reason"]
            if auto_rollback:
                _rollback_snapshot(s, w)
        elif v == "promote":
            w.status = "promoted"  # 只标记；实际应用走 ParamSuggestion 人工闸门
        else:  # hold
            w.status = "hold"  # 数据不足，保留观察（交人工）
        s.add(w)
        s.commit()

    return {"window_id": window.id, "verdict": v, "reason": verdict["reason"],
            "metrics": {"base": base_metrics, "shadow": shadow_metrics}}


def _run_shadow_dry_run(session, window, *, base: bool) -> dict:
    """跑一轮 dry-run（复用 eval.runner），返回 compute_metrics 输出。

    base=True 用 base_snapshot；否则用 param_overrides（影子参数）。
    失败返回空 metrics（evaluate_window 会对空输入 hold）。
    """
    from lantai.core.settings import settings as _s
    from lantai.eval.query_set import load_query_set
    from lantai.eval.runner import run_dry_run

    try:
        qs = load_query_set("dry-run-v1", session=session)
        if qs is None:
            return {}
        run = run_dry_run(
            qs,
            param_overrides=None if base else window.param_overrides,
            top_k=_s.EVAL_TOPK,
            use_rerank=False,
            intent_mode="rule",
        )
        return run.metrics
    except Exception:
        return {}


def _rollback_snapshot(session, window) -> None:
    """护栏回滚：写一条 ParamOverride(operation=rollback) 恢复 base_snapshot。"""
    from lantai.models.tables import ParamOverride
    from lantai.parameters.registry import get_registry_version
    from lantai.parameters.validation import snapshot_hash

    rollback = ParamOverride(
        id=new_id("po"),
        revision=_next_revision(session),
        operation="rollback",
        suggestion_id=None,
        rollback_of_override_id=window.override_id or None,
        before_snapshot=window.param_overrides or {},
        after_snapshot=window.base_snapshot,
        before_snapshot_hash=snapshot_hash(window.param_overrides or {}),
        after_snapshot_hash=snapshot_hash(window.base_snapshot),
        changes=[{"shadow_window_id": window.id, "reason": window.rollback_reason}],
        registry_version=get_registry_version(),
        actor="shadow_guardrail",
        note="shadow rollback of " + str(window.override_id),
    )
    session.add(rollback)
    session.commit()


def _next_revision(session) -> int:
    """下一个 revision（head + 1）。"""
    head = _head_override(session)
    return (head.revision + 1) if head else 1
