"""票 29：观测面保留策略不对称——retrieval_event/llm_call_event 零行级清理无界增长。

病灶（立案轮 5 深度巡检，分诊轮 HEAD `2865e66d` 逐处亲核）：
1. 三条观测链只有一条有保留期——`telemetry.py:123-141` prune 是完整范式点
   （MONITOR_RETENTION_DAYS 化 cutoff、返回删除数、失败吞掉返回 0）；
   `retrieval_log.py` 全文零行级清理（`mark_missed_receipts` 只是状态迁移，
   行保留）；`llm/client.py:19-41` 每次 LLM 外呼写一行 `LLMCallEvent`，
   全仓无清理入口。不对称成形于 `315f3b87`（司天 v0.4 只给 operation_logs
   配 prune）；`llm_call_event` 引入于 `04e1d1a9`（v0.24.0）即无保留期。
2. 增长画像：两表是全库最高频写入表；TTL 短于观察窗（recall_report 的
   RECALL_MONITOR_WINDOW_DAYS 窗、司天 24h 降级率窗）会让窗口计数静默失真
   ——下限必须结构性钳制，不许只靠文档。
3. 门禁假绿面：release_check 无任何「观测追加表 ↔ 保留期」对账——新增
   无保留期的追加表全部门禁放行。

缝（工单锚定六缝）：清扫函数本体（真内存库直调，禁 mock 被测链路）/
幂等 / `<` 严格边界 / 观察窗钳制 / operation_logs 现状对照臂 /
scheduler 挂载（镜像 test_receipt_miss_scrub 的 _FakeScheduler 形态）/
司天面透出 / 发布门禁对账。拍板：不复用 MONITOR_RETENTION_DAYS（观察窗
与取证窗语义不同源），新增 RETRIEVAL_EVENT_RETENTION_DAYS /
LLM_CALL_EVENT_RETENTION_DAYS 独立配置，运行时钳制
max(config, RECALL_MONITOR_WINDOW_DAYS)。
"""

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

from sqlmodel import func, select

from lantai.core.time import utcnow
from lantai.models.tables import LLMCallEvent, OperationLog, RetrievalEvent

REPO = Path(__file__).resolve().parent.parent

# 缺省锚（独立真源，测试断言用字面量——不从被测代码反推，防同义反复）
DEFAULT_RETRIEVAL_EVENT_RETENTION_DAYS = 30
DEFAULT_LLM_CALL_EVENT_RETENTION_DAYS = 30


def _load_release_check():
    """scripts/ 非包，按路径加载（形态同 test_release_gate_schema_drift）。"""
    name = "release_check_retention"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 种子工具（真内存库直插，created_at 显式落定）───────────────────────


def _seed_retrieval(sf, eid: str, *, days_old: float | None = None, at=None) -> None:
    created = at if at is not None else utcnow() - timedelta(days=days_old or 0)
    with sf() as s:
        s.add(
            RetrievalEvent(
                id=eid,
                trace_id=f"trace_{eid}",
                query_text="票 29 保留测试查询",
                query_norm_hash=f"hash_{eid}"[:16],
                param_snapshot_hash="snap_ticket29",
                result_ids=[],
                used_ids=[],
                created_at=created,
            )
        )
        s.commit()


def _seed_llm(sf, eid: str, *, days_old: float | None = None, at=None) -> None:
    created = at if at is not None else utcnow() - timedelta(days=days_old or 0)
    with sf() as s:
        s.add(
            LLMCallEvent(id=eid, channel="chat", outcome="ok", duration_ms=10, created_at=created)
        )
        s.commit()


def _count(sf, model) -> int:
    with sf() as s:
        return int(s.exec(select(func.count()).select_from(model)).one())


# ── 红测主臂：清扫本体（超龄删 / 新鲜留 / 返回值 = 删除数）──────────────


def test_prune_retrieval_events_deletes_stale_keeps_fresh_returns_count(param_env):
    sf, _engine = param_env
    from lantai.observability.retention import prune_retrieval_events

    for i in range(3):
        _seed_retrieval(sf, f"rev-stale-{i}", days_old=40)
    for i in range(2):
        _seed_retrieval(sf, f"rev-fresh-{i}", days_old=1)

    deleted = prune_retrieval_events()

    assert deleted == 3, f"返回值必须等于删除条数（票 29）：got {deleted}"
    assert _count(sf, RetrievalEvent) == 2, "新鲜行必须保留"
    with sf() as s:
        remaining = {r.id for r in s.exec(select(RetrievalEvent)).all()}
    assert remaining == {"rev-fresh-0", "rev-fresh-1"}, remaining


def test_prune_llm_call_events_deletes_stale_keeps_fresh_returns_count(param_env):
    sf, _engine = param_env
    from lantai.observability.retention import prune_llm_call_events

    for i in range(3):
        _seed_llm(sf, f"llm-stale-{i}", days_old=40)
    for i in range(2):
        _seed_llm(sf, f"llm-fresh-{i}", days_old=1)

    deleted = prune_llm_call_events()

    assert deleted == 3, f"返回值必须等于删除条数（票 29）：got {deleted}"
    assert _count(sf, LLMCallEvent) == 2, "新鲜行必须保留"
    with sf() as s:
        remaining = {r.id for r in s.exec(select(LLMCallEvent)).all()}
    assert remaining == {"llm-fresh-0", "llm-fresh-1"}, remaining


# ── 幂等锁：连跑两遍第二遍 0（prune_all 逐表计数 + 总幂等）──────────────


def test_prune_all_reports_per_table_and_second_run_is_zero(param_env):
    sf, _engine = param_env
    from lantai.observability.retention import prune_all

    for i in range(2):
        _seed_retrieval(sf, f"rev-all-{i}", days_old=40)
    for i in range(2):
        _seed_llm(sf, f"llm-all-{i}", days_old=40)

    first = prune_all()
    assert first["retrieval_event"] == 2 and first["llm_call_event"] == 2, first

    second = prune_all()
    assert second["retrieval_event"] == 0 and second["llm_call_event"] == 0, (
        f"第二遍必须幂等返回 0（票 29）：{second}"
    )
    assert _count(sf, RetrievalEvent) == 0 and _count(sf, LLMCallEvent) == 0


# ── 边界锁：cutoff 恰在行上的保留语义（< 严格小于，防 <= 抖动）──────────


def test_retrieval_cutoff_is_strictly_less_than(param_env):
    sf, _engine = param_env
    from lantai.observability.retention import prune_retrieval_events

    now = utcnow()
    cutoff = now - timedelta(days=DEFAULT_RETRIEVAL_EVENT_RETENTION_DAYS)
    _seed_retrieval(sf, "rev-boundary", at=cutoff)  # 恰在 cutoff 上：必须保留
    _seed_retrieval(sf, "rev-just-stale", at=cutoff - timedelta(seconds=1))  # 早 1 秒：必须删
    _seed_retrieval(sf, "rev-just-fresh", at=cutoff + timedelta(days=1))

    deleted = prune_retrieval_events(now=now)

    assert deleted == 1, f"边界行不得被 <= 误删（票 29 边界锁）：deleted={deleted}"
    with sf() as s:
        remaining = {r.id for r in s.exec(select(RetrievalEvent)).all()}
    assert remaining == {"rev-boundary", "rev-just-fresh"}, remaining


def test_llm_cutoff_is_strictly_less_than(param_env):
    sf, _engine = param_env
    from lantai.observability.retention import prune_llm_call_events

    now = utcnow()
    cutoff = now - timedelta(days=DEFAULT_LLM_CALL_EVENT_RETENTION_DAYS)
    _seed_llm(sf, "llm-boundary", at=cutoff)
    _seed_llm(sf, "llm-just-stale", at=cutoff - timedelta(seconds=1))

    deleted = prune_llm_call_events(now=now)

    assert deleted == 1, f"边界行不得被 <= 误删（票 29 边界锁）：deleted={deleted}"
    with sf() as s:
        remaining = {r.id for r in s.exec(select(LLMCallEvent)).all()}
    assert remaining == {"llm-boundary"}, remaining


# ── 观察窗下限钳制：TTL 短于观察窗必须结构性截停（宁钳制不静默失真）──────


def test_retrieval_ttl_floored_at_observation_window(param_env, monkeypatch):
    """RECALL_MONITOR_WINDOW_DAYS 是 recall_report 观察窗（ADR-0027）：TTL 短于
    它会让窗口计数静默失真——运行时钳制 max(config, window)，并留 warning。"""
    from lantai.core.settings import settings
    from lantai.observability.retention import prune_retrieval_events

    sf, _engine = param_env
    monkeypatch.setattr(settings, "RETRIEVAL_EVENT_RETENTION_DAYS", 1)
    monkeypatch.setattr(settings, "RECALL_MONITOR_WINDOW_DAYS", 30)

    now = utcnow()
    _seed_retrieval(
        sf, "rev-underwindow", at=now - timedelta(days=10)
    )  # 1d TTL 会删、30d 窗内必须留
    assert prune_retrieval_events(now=now) == 0, (
        "TTL 短于观察窗必须被钳制截停（票 29：观察窗计数不得静默失真）"
    )
    assert _count(sf, RetrievalEvent) == 1

    # 反向：config 高于窗口不钳顶——60 天保留如实生效（40 天行不动）
    monkeypatch.setattr(settings, "RETRIEVAL_EVENT_RETENTION_DAYS", 60)
    monkeypatch.setattr(settings, "RECALL_MONITOR_WINDOW_DAYS", 7)
    _seed_retrieval(sf, "rev-longkeep", at=now - timedelta(days=40))
    assert prune_retrieval_events(now=now) == 0, "config 高于观察窗时不得被往下钳"
    assert _count(sf, RetrievalEvent) == 2


# ── 现状锁（对照臂）：operation_logs 既有 prune 行为零改动 ──────────────


def test_operation_logs_prune_behavior_unchanged(param_env):
    """范式点对照臂（票 29 TDD 要求 4）：TelemetryWriter.prune 语义不动——
    超龄删、新鲜留、返回删除数；stats 仍暴露 MONITOR_RETENTION_DAYS。"""
    from lantai.core.settings import settings
    from lantai.observability.telemetry import TelemetryWriter

    sf, _engine = param_env
    now = utcnow()
    with sf() as s:
        s.add(
            OperationLog(
                id="op-stale", user_id="u", endpoint="GET /x", created_at=now - timedelta(days=40)
            )
        )
        s.add(
            OperationLog(
                id="op-fresh", user_id="u", endpoint="GET /x", created_at=now - timedelta(days=1)
            )
        )
        s.commit()

    deleted = TelemetryWriter().prune()

    assert deleted == 1, f"operation_logs prune 行为漂移（票 29 现状锁）：deleted={deleted}"
    assert _count(sf, OperationLog) == 1
    with sf() as s:
        assert s.get(OperationLog, "op-fresh") is not None
    assert TelemetryWriter().stats()["retention_days"] == int(settings.MONITOR_RETENTION_DAYS)


# ── settings 声明面：独立旋钮存在且默认值与观察窗相容 ────────────────────


def test_settings_declare_independent_retention_knobs():
    """ADR-0002：TTL 必须走 settings；不复用 MONITOR_RETENTION_DAYS（语义
    不同源），默认值 ≥ 观察窗（防出厂配置即触发钳制）。"""
    from lantai.core.settings import settings

    for knob, default in (
        ("RETRIEVAL_EVENT_RETENTION_DAYS", DEFAULT_RETRIEVAL_EVENT_RETENTION_DAYS),
        ("LLM_CALL_EVENT_RETENTION_DAYS", DEFAULT_LLM_CALL_EVENT_RETENTION_DAYS),
    ):
        value = getattr(settings, knob, None)
        assert isinstance(value, int) and value >= 1, f"settings 缺 {knob}（票 29 病灶 3）"
        assert value == default, f"{knob} 默认值漂移：{value} != {default}"
        assert value >= settings.RECALL_MONITOR_WINDOW_DAYS, f"{knob} 默认值低于观察窗，出厂即钳制"


# ── scheduler 挂载锁（镜像 test_receipt_miss_scrub 的 _FakeScheduler）────


class _FakeScheduler:
    def __init__(self, **kwargs):
        self.jobs = []

    def add_job(self, fn, trigger=None, **kwargs):
        self.jobs.append({"id": kwargs.get("id"), "trigger": trigger, "kwargs": kwargs, "fn": fn})

    def start(self):
        pass


def test_scheduler_mounts_observability_purge_hourly(param_env, monkeypatch):
    """清扫存在但永不运行 = 不对称复发的静默形态：scheduler 必须挂载
    observability_purge（镜像 kanhe_purge 每小时形态），fn 是保留模块的
    prune_all 本体。"""
    import lantai.core.scheduler as scheduler_mod
    from lantai.observability.retention import prune_all

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler_mod, "BackgroundScheduler", lambda **kw: fake)
    monkeypatch.setattr(scheduler_mod, "_scheduler", fake)
    scheduler_mod.start_scheduler()

    job = next(j for j in fake.jobs if j["id"] == "observability_purge")
    assert job["trigger"] == "interval"
    assert job["kwargs"].get("hours") == 1
    assert job["kwargs"].get("replace_existing") is True
    assert job["fn"] is prune_all, "挂载的必须是保留清扫入口本体"


# ── 司天面透出：行数 + 最近清理时间（对齐 telemetry.stats() 范式）────────


def test_retention_stats_exposes_rows_and_last_sweep(param_env):
    from lantai.observability.retention import prune_all, retention_stats

    sf, _engine = param_env
    _seed_retrieval(sf, "rev-stats", days_old=40)
    _seed_retrieval(sf, "rev-stats-fresh", days_old=1)
    _seed_llm(sf, "llm-stats", days_old=40)

    prune_all()
    stats = retention_stats()

    for table in ("retrieval_event", "llm_call_event"):
        entry = stats.get(table)
        assert isinstance(entry, dict), f"retention_stats 缺 {table}（票 29 修复目标 3）：{stats}"
        assert entry.get("days", 0) >= 1
        assert entry.get("last_pruned_at"), f"{table} 缺最近清理时间"
        assert entry.get("last_deleted") == 1, f"{table} last_deleted 失真：{entry}"
    assert stats["retrieval_event"]["rows"] == 1, "行数必须真实计数（超龄已删、新鲜留下）"
    assert stats["llm_call_event"]["rows"] == 0

    # 未清扫过：last_pruned_at 如实为 None（不编造）
    from lantai.observability import retention as retention_mod

    retention_mod.reset_stats()
    stats2 = retention_stats()
    assert stats2["retrieval_event"]["last_pruned_at"] is None
    assert stats2["retrieval_event"]["rows"] == 1


def test_monitor_face_surfaces_retention_block(param_env):
    """司天面（collect_security_view，与 telemetry.stats() 同一透出范式）
    必须带 retention 块——局部清理没有全局可见度 = 整体不可证。"""
    from lantai.observability import retention as retention_mod
    from lantai.ops.monitor import collect_security_view

    retention_mod.reset_stats()
    sf, _engine = param_env
    _seed_retrieval(sf, "rev-face", days_old=1)
    with sf() as s:
        view = collect_security_view(s)
    assert "retention" in view, "司天面缺 retention 块（票 29 修复目标 3）"
    entry = view["retention"]["retrieval_event"]
    assert entry["rows"] == 1 and entry["days"] >= 1


# ── 发布门禁对账：未修复状态下不能发布（票 29 红线）─────────────────────


def test_release_gate_rejects_retention_asymmetry():
    """合成源对账：任一观测表缺旋钮/缺清扫/未挂载 → 门禁给出可操作错误；
    四源齐全 → 零 issue。"""
    mod = _load_release_check()

    retention_text = (REPO / "lantai" / "observability" / "retention.py").read_text(
        encoding="utf-8"
    )
    settings_text = (REPO / "lantai" / "core" / "settings.py").read_text(encoding="utf-8")
    scheduler_text = (REPO / "lantai" / "core" / "scheduler.py").read_text(encoding="utf-8")
    telemetry_text = (REPO / "lantai" / "observability" / "telemetry.py").read_text(
        encoding="utf-8"
    )

    clean = mod.retention_symmetry_issues_from_sources(
        settings_text, retention_text, scheduler_text, telemetry_text
    )
    assert clean == [], f"现仓库源应通过保留对称性对账：{clean}"

    # 缺旋钮
    missing_knob = mod.retention_symmetry_issues_from_sources(
        settings_text.replace(
            "    RETRIEVAL_EVENT_RETENTION_DAYS", "    # RETRIEVAL_EVENT_RETENTION_DAYS"
        ),
        retention_text,
        scheduler_text,
        telemetry_text,
    )
    assert any("RETRIEVAL_EVENT_RETENTION_DAYS" in issue for issue in missing_knob), missing_knob

    # 缺挂载
    unmounted = mod.retention_symmetry_issues_from_sources(
        settings_text,
        retention_text,
        scheduler_text.replace("prune_all", "_prune_all_unused"),
        telemetry_text,
    )
    assert any("observability_purge" in issue or "prune_all" in issue for issue in unmounted), (
        unmounted
    )

    # 缺清扫函数本体
    missing_sweep = mod.retention_symmetry_issues_from_sources(
        settings_text,
        retention_text.replace("def prune_llm_call_events", "def _gone_llm_call_events"),
        scheduler_text,
        telemetry_text,
    )
    assert any("llm_call_event" in issue for issue in missing_sweep), missing_sweep


def test_release_gate_has_retention_symmetry_check():
    """门禁本体必须存在并被 main 编排（未修复树：AttributeError = 红的
    直接证据——没有这道检查就没有「整体可观测性成立」的可证性）。"""
    mod = _load_release_check()
    assert callable(getattr(mod, "retention_symmetry_issues", None)), (
        "release_check 缺 retention_symmetry_issues（票 29 门禁收口）"
    )
    issues = mod.retention_symmetry_issues(REPO)
    assert issues == [], f"当前树必须通过保留对称性门禁：{issues}"
