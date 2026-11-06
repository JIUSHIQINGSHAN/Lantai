"""worker 入口真冒烟（v0.68 测试诚实性审计补强）。

审计结论（docs/memory-quality/test-honesty-audit-2026-11.md）：两个 worker
入口此前**从未被任何测试真实执行**——`run_forgetting_once` / `run_candidate_ttl`
只在 `worker_operation_service._runners()` 的字符串分发注册表里被引用，控制台
手动触发端点的测试只打未知名（422 分支），从没带真名跑过一次。其中
`run_candidate_ttl` 正是 AGENTS.md「宁 miss 不脏写」候选超龄归档的落地点。

本文件真实构造 DB 输入直调入口，不 mock 内部逻辑（仅依赖 conftest 既定
外部替身口径：LLM/embedding/向量库）。
"""

from datetime import UTC, datetime, timedelta

import pytest


def test_run_forgetting_once_empty_db_smoke(param_env):
    """遗忘 worker 入口：空库真实执行 apply_forgetting 分页扫描 + 完成戳落表。"""
    from lantai.core import scheduler as scheduler_mod
    from lantai.workers.forgetting_worker import run_forgetting_once

    run_forgetting_once()

    stamp = scheduler_mod.WORKER_LAST_RUN.get("forgetting")
    assert stamp, "record_run 完成戳未写入"
    datetime.fromisoformat(stamp)  # record_run 产物形状：可解析 ISO 时间戳


def test_run_candidate_ttl_archives_only_overdue(param_env):
    """TTL 归档入口：超龄 pending_review → rejected；未到期/无截止 → 原样不动。"""
    from lantai.models.tables import MemoryCandidate
    from lantai.workers.digest_worker import run_candidate_ttl

    session_factory, _ = param_env
    overdue_id, future_id, nodue_id = "cand-ttl-overdue", "cand-ttl-future", "cand-ttl-nodue"
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id=overdue_id,
                document_id="doc-ttl-overdue",
                user_id="ttl-smoke",
                status="pending_review",
                review_due_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        s.add(
            MemoryCandidate(
                id=future_id,
                document_id="doc-ttl-future",
                user_id="ttl-smoke",
                status="pending_review",
                review_due_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        s.add(
            MemoryCandidate(
                id=nodue_id,
                document_id="doc-ttl-nodue",
                user_id="ttl-smoke",
                status="pending_review",
                review_due_at=None,
            )
        )
        s.commit()

    result = run_candidate_ttl()

    assert result == {"ok": True, "archived": 1}
    with session_factory() as s:
        assert s.get(MemoryCandidate, overdue_id).status == "rejected"
        assert s.get(MemoryCandidate, future_id).status == "pending_review"
        assert s.get(MemoryCandidate, nodue_id).status == "pending_review"


@pytest.mark.parametrize(
    ("worker_name", "entry"),
    [
        ("forgetting", "lantai.workers.forgetting_worker.run_forgetting_once"),
        ("candidate_ttl", "lantai.workers.digest_worker.run_candidate_ttl"),
    ],
)
def test_worker_operation_registry_resolves_real_entries(worker_name, entry):
    """控制台手动触发注册表里的名字必须解析到真实 worker 入口。

    结构性护栏：`run_worker(worker_name)` 按字符串分发，名字漂移（worker 改名
    而注册表没跟上）会让控制台按钮静默打空——与 recall_chain -32603 同型的
    「广告 ⇔ 实现」缺口。
    """
    from lantai.services import worker_operation_service

    runners = worker_operation_service._runners()
    assert worker_name in runners, f"worker {worker_name!r} 未登记进控制台触发注册表"
    import importlib

    mod_name, fn_name = entry.rsplit(".", 1)
    expected = getattr(importlib.import_module(mod_name), fn_name)
    assert runners[worker_name] is expected
