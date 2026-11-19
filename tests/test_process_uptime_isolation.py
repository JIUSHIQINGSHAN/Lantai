"""测试结果不随 pytest 进程活了多久而变（巡检 2026-11-18）。

背景见 conftest `_pin_process_started_at`：worker 逾期在没有 SchedulerRun 时以
进程启动时间为基线，windows-latest 全量跑到 30 分钟以上时
`test_work_items_read_ownership` 多出一条 receipt_miss 逾期，2 == 1 假红。

这里把「老进程」显式造出来：用 `project_work_items(process_started_at=...)`
证明机制真实存在；再断言默认路径（不传 process_started_at）拿到的是本例的
「现在」，而不是 import 时的旧值。
"""

from datetime import timedelta

from lantai.core.time import utcnow

_EMPTY = {
    "candidates": [],
    "proposals": [],
    "conflicts": [],
    "parameters": [],
    "crystals": [],
    "memories": [],
    "scheduler_runs": [],
    "latest_ingest_job": None,
    "latest_param_run": None,
    "latest_reflect_run": None,
    "current_param_hash": None,
}


def _snapshot():
    from lantai.services.work_item_service import worker_schedule_specs

    return {**_EMPTY, "worker_schedules": worker_schedule_specs()}


def _overdue_ids(items):
    return {i.id for i in items if i.kind == "worker" and i.status == "overdue"}


def test_old_process_baseline_does_produce_overdue_workers():
    """对照：基线是 26 分钟前时，receipt_miss 确实逾期——这是产品行为，不动。"""
    from lantai.services.work_item_service import project_work_items

    now = utcnow()
    items = project_work_items(_snapshot(), now=now, process_started_at=now - timedelta(minutes=26))
    assert "worker:receipt_miss:overdue" in _overdue_ids(items)


def test_default_baseline_is_this_test_not_import_time():
    """默认路径不应看到任何 worker 逾期：基线必须是本例开始的时刻。"""
    import lantai.observability.metrics as metrics
    import lantai.ops.monitor as monitor
    import lantai.services.work_item_service as wis

    now = utcnow()
    for value in (wis._PROCESS_STARTED_AT, metrics.PROCESS_STARTED_AT, monitor.PROCESS_STARTED_AT):
        assert abs((now - value).total_seconds()) < 60, (
            f"进程启动时间没有每例钉住：{value} 距今 {(now - value).total_seconds():.0f}s"
        )
    assert _overdue_ids(wis.project_work_items(_snapshot(), now=now)) == set()
