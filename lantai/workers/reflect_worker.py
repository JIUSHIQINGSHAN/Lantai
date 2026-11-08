"""反思 worker（spec: docs/plans/reflection-module-spec.md）

- run_reflect_once：健康扫描 → 蒸馏 → 提案化 → 自动应用/待审 → 健康快照自证
  核心逻辑在 lantai/evolution/reflector.py；record_run 由核心函数内部记录。

归属（票 `.scratch/mcp-identity-gaps/06`）：定时反思要的是**全表**，
但不能再靠 `principal=None` 表达——那个值在 MCP 入口面的含义是
「宿主没透传身份」。两种含义混用一个值，无身份的 MCP 调用就继承了
worker 的全表权限（票 04/05/06 连续三票同一根因）。
故显式传系统身份 `acl.SYSTEM_VIEWER`。
"""

from datetime import UTC, date, datetime, time, timedelta

from sqlmodel import Session, select

from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import ensure_aware
from lantai.evolution.reflector import run_reflect_once as _run_reflect
from lantai.models.tables import ReflectRun


def observation_status(
    session: Session,
    *,
    window_days: int | None = None,
    required_runs: int | None = None,
    reference: datetime | None = None,
) -> dict:
    """察窗评估（ADR-0027）：窗口内合格 scheduled 运行天数与毕业判定。

    合格判定与 scripts/reflect_observation_status.py（standalone sqlite3
    直读，发布准备工具）同判据——source=scheduled、error 空、curate_failed=0、
    rejecter_failed=0，同一日任一合格即该日合格（日级 OR）。两实现因连接
    形态分置，判据一致性由 tests/test_scheduler_config_gaps.py 判据双锁臂
    钉死。窗口锚 UTC 日（库存 UTC；脚本锚本地今日为既有形态，边界日差一天
    ——票 34 Answer 剩余风险）。

    只评估不闸运行（票 34 拍板 (c)）：合格运行日由运行本身产生，自闸即
    死锁；闸门对象是校准（CONTEXT.md 察窗立法语义），不是采样器。
    """
    window_days = (
        window_days if window_days is not None else settings.REFLECT_OBSERVATION_WINDOW_DAYS
    )
    required_runs = (
        required_runs if required_runs is not None else settings.REFLECT_OBSERVATION_REQUIRED_RUNS
    )
    ref_date = (ensure_aware(reference) if reference else datetime.now(UTC)).date()
    window_start = datetime.combine(
        ref_date - timedelta(days=window_days - 1), time.min, tzinfo=UTC
    )
    window_end = datetime.combine(ref_date + timedelta(days=1), time.min, tzinfo=UTC)
    rows = session.exec(
        select(ReflectRun).where(ReflectRun.run_at >= window_start, ReflectRun.run_at < window_end)
    ).all()
    qualified_days: set[date] = set()
    for r in rows:
        if r.source != "scheduled":
            continue
        if r.error or r.curate_failed or (r.rejecter_failed or 0) > 0:
            continue
        qualified_days.add(ensure_aware(r.run_at).date())
    qualified = len(qualified_days)
    return {
        "window_days": window_days,
        "required_runs": required_runs,
        "qualified_days": qualified,
        "remaining_runs": max(required_runs - qualified, 0),
        "graduated": qualified >= required_runs,
    }


def run_reflect_once() -> dict:
    # 票 34 拍板 (c)：每次 scheduled 运行真评察窗并留痕（宁 visible 不静默）
    # ——REFLECT_OBSERVATION_* 门槛自此每次运行必被评估；评估不阻断运行，
    # 合格运行日由运行本身产生，自闸即死锁（详见 observation_status 注记）。
    try:
        from lantai.storage import db

        with db.get_session() as s:
            obs = observation_status(s)
    except Exception:
        logger.exception("察窗评估失败（不阻断反思运行）")
        obs = {"error": "observation evaluation failed"}
    logger.info(
        "察窗状态：%s/%s 合格运行日（窗口 %s 天，%s）",
        obs.get("qualified_days", "?"),
        obs.get("required_runs", "?"),
        obs.get("window_days", "?"),
        "已毕业" if obs.get("graduated") else "未毕业（继续采样，不闸运行）",
    )
    res = _run_reflect(
        source="scheduled",
        principal=Principal(
            tenant_id=None,
            user_id=SYSTEM_VIEWER,
            agent_id=None,
            session_id=None,
            role="system",
            allowed_lanes=None,
        ),
    )
    try:
        from lantai.cognition.reflection import ReflectionEngine
        from lantai.storage import db

        with db.get_session() as s:
            engine = ReflectionEngine(s)
            report = engine.run_reflection()
            res["cognitive_reflection"] = {
                "new_patterns": report.new_patterns,
                "belief_candidates": report.belief_candidates,
                "rule_candidates": report.rule_candidates,
                "rules_weakened": report.rules_weakened,
                "failures": report.failures,
                # 票 cognitive-write-gaps/01：`FailureRecord` 无归属列，
                # 这个数跨用户——如实标注，别让读 worker 输出的人误判。
                "failures_scoped": report.failures_scoped,
                "summary": report.summary,
            }
    except Exception as exc:
        # 认知反思失败不得静默吞掉：留日志 + 在结果里留结构化失败，
        # 且不影响上面 `_run_reflect` 已产出的结果。
        logger.exception("cognitive reflection failed")
        res["cognitive_reflection"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    res["observation"] = obs  # 票 34：察窗评估进回执（运行期可见，不闸运行）
    return res
