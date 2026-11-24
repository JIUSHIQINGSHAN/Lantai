"""潜移任务注册表有界化 + worker 兜底（巡检 2026-11-24）。

病灶（审计发现）：`_TASKS` 只增不减——每轮对话一条，常驻服务跑几天就是几万条
dict，且 `result` 里带着候选正文；`_execute_dialogue_task` 又直接 `_TASKS[task_id]`
取行，注册表一旦被清扫，worker 会在后台线程里抛 KeyError 静默死掉、claim 也不
结算（同文重试永远走 pending）。

本测试不 mock 被测逻辑：直接构造注册表行、直调清扫与 worker 入口；`settle`
用记录器接管（它是幂等层侧效，不是被测分支）。
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from lantai.core.time import iso_utc, utcnow
from lantai.services import async_ingest_service as ais


def _iso_ago(seconds: float) -> str:
    return iso_utc(utcnow() - timedelta(seconds=seconds))


def _task(status: str, *, completed_at: str | None = None, task_id: str = "t") -> dict:
    row = {"task_id": task_id, "status": status, "result": None, "error": None}
    if completed_at is not None:
        row["completed_at"] = completed_at
    return row


@pytest.fixture(autouse=True)
def _clean_registry():
    ais.clear_tasks()
    yield
    ais.clear_tasks()


def test_sweep_drops_only_expired_terminal_tasks():
    """TTL 外的终态任务被清；在跑/排队中的任务与新鲜终态一律保留。"""
    now = time.time()
    ais._TASKS.update(
        {
            "old_done": _task("completed", completed_at=_iso_ago(7200), task_id="old_done"),
            "old_failed": _task("failed", completed_at=_iso_ago(7200), task_id="old_failed"),
            "fresh_done": _task("completed", completed_at=_iso_ago(60), task_id="fresh_done"),
            "running": _task("processing", completed_at=None, task_id="running"),
            "queued": _task("queued", completed_at=None, task_id="queued"),
        }
    )

    removed = ais._sweep_tasks(now=now)

    assert removed == 2
    assert set(ais._TASKS) == {"fresh_done", "running", "queued"}


def test_sweep_enforces_cap_evicting_oldest_terminal_first(monkeypatch):
    """超硬顶时从最旧的终态开始淘汰；活跃任务绝不淘汰（否则轮询变 not_found）。"""
    monkeypatch.setattr(ais, "_TASKS_MAX", 3)
    now = time.time()
    ais._TASKS.update(
        {
            "oldest": _task("completed", completed_at=_iso_ago(300), task_id="oldest"),
            "middle": _task("failed", completed_at=_iso_ago(200), task_id="middle"),
            "newest": _task("completed", completed_at=_iso_ago(100), task_id="newest"),
            "running": _task("processing", completed_at=None, task_id="running"),
        }
    )

    removed = ais._sweep_tasks(now=now)

    assert removed == 1, "超顶 1 条，只应淘汰最旧的终态"
    assert set(ais._TASKS) == {"middle", "newest", "running"}, (
        f"应淘汰 oldest 且保留活跃任务，实得 {sorted(ais._TASKS)}"
    )


def test_worker_releases_claim_when_registry_row_missing(monkeypatch):
    """注册表里没有该任务：worker 不抛 KeyError，释放 claim 让重试可再执行。"""
    settled: list[tuple] = []

    def fake_settle(binding, *, ok, result=None, task_id=""):
        settled.append((binding, ok, result, task_id))
        return "released"

    monkeypatch.setattr(ais, "settle", fake_settle)

    ais._execute_dialogue_task(
        "task_ghost",
        "记住：这条任务已经不在注册表",
        "default",
        "dialogue",
        ingest_event_id="ing_ghost",
    )

    assert settled == [(None, False, None, "")], f"应释放 claim（ok=False），实得 {settled!r}"
    assert "task_ghost" not in ais._TASKS


def test_worker_records_processing_on_registered_task(monkeypatch):
    """正常路径不漂移：已登记任务被 worker 置 processing→completed，结果回写。"""

    def fake_ingest(**kwargs):
        return {"ingested": True, "candidate_id": "cand_x"}

    monkeypatch.setattr("lantai.ingestion.dialogue.ingest_dialogue", fake_ingest, raising=True)
    monkeypatch.setattr(ais, "settle", lambda *a, **k: "finalized")

    ais._TASKS["task_live"] = _task("queued", task_id="task_live")
    ais._execute_dialogue_task(
        "task_live",
        "记住：正常任务",
        "default",
        "dialogue",
        ingest_event_id="ing_live",
    )

    row = ais._TASKS["task_live"]
    assert row["status"] == "completed"
    assert row["result"]["candidate_id"] == "cand_x"
    assert row["completed_at"]
