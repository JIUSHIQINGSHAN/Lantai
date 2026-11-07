"""工单 10：非 veto 终态的检索原话留存收口（missed 即抹文 + scheduler 挂载 + 存量 scrub）。

五条缝（工单锚定）：`mark_missed_receipts` 抹文 / missed→acked 回执与保活链 /
scheduler 注册 + `run_receipt_miss_scan` / 存量 scrub 脚本 / vetoed 结构性隔离。
真内存库（param_env）直调被测链路本体，禁 mock。
"""

import importlib.util
from datetime import timedelta
from pathlib import Path

from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, RetrievalEvent

REPO = Path(__file__).resolve().parent.parent


def _log_event():
    from lantai.observability.retrieval_log import log_retrieval

    return log_retrieval(
        "部署怎么做",
        [{"score": 0.9, "memory": {"id": "m1"}}, {"score": 0.8, "memory": {"id": "m2"}}],
        latency_ms=5,
    )


def _rewind(event_id, param_env, minutes=10):
    sf, _engine = param_env
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        ev.created_at = utcnow() - timedelta(minutes=minutes)
        s.add(ev)
        s.commit()


def _seed_event(param_env, *, status: str, query: str, minutes_old=30) -> str:
    """直造指定终态行（含历史脏数据形态：vetoed 带原文）。"""
    sf, _engine = param_env
    eid = f"ev_{status}_{abs(hash(query)) % 10**8}"
    with sf() as s:
        s.add(
            RetrievalEvent(
                id=eid,
                trace_id=f"trace_{eid}",
                query_text=query,
                query_norm_hash=f"hash_{query}"[:16],
                param_snapshot_hash="snap_test",
                result_ids=[],
                used_ids=[],
                receipt_status=status,
                created_at=utcnow() - timedelta(minutes=minutes_old),
            )
        )
        s.commit()
    return eid


# ── 红测 A：pending→missed 同事务抹文，used_ids 原样 ────────────────────────


def test_mark_missed_scrubs_query_text_keeps_used_ids(param_env):
    sf, _engine = param_env
    event_id = _log_event()
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        ev.used_ids = ["m1"]  # 预置弱标注：mark_missed 不得顺手清它
        s.add(ev)
        s.commit()
    _rewind(event_id, param_env)

    from lantai.observability.retrieval_log import mark_missed_receipts

    assert mark_missed_receipts(timeout_seconds=300) >= 1
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev.receipt_status == "missed"
        assert ev.query_text == "", "missed 行原话必须抹（与 mark_vetoed 同源论证）"
        assert ev.query_norm_hash == "", "归一化哈希对低熵原文是可验证口令，必须同抹"
        assert ev.used_ids == ["m1"], "used_ids 是 ADR-0049 弱标注源，不得清"
        assert ev.receipt_at is not None


# ── 红测 B：missed→acked 合法，保活链不断 ──────────────────────────────────


def test_missed_row_backfills_and_keepalive_chain_unbroken(param_env):
    sf, _engine = param_env
    with sf() as s:
        s.add(MemoryItem(id="m1", content="content-m1", status="active"))
        s.commit()
    event_id = _log_event()
    _rewind(event_id, param_env)

    from lantai.observability.retrieval_log import backfill_used_ids, mark_missed_receipts

    mark_missed_receipts(timeout_seconds=300)
    backfill_used_ids(event_id, ["m1"])
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev.receipt_status == "acked", "missed→acked 合法（只有 vetoed 该拒）"
        m = s.get(MemoryItem, "m1")
        assert (m.injection_count or 0) == 1, "保活链不断：missed 行回执仍刷 injection_count"
        assert m.last_used_at is not None


# ── 红测 C：scheduler 挂载 + 真跑一轮 ──────────────────────────────────────


class _FakeScheduler:
    def __init__(self, **kwargs):
        self.jobs = []

    def add_job(self, fn, trigger=None, **kwargs):
        self.jobs.append({"id": kwargs.get("id"), "trigger": trigger, "kwargs": kwargs, "fn": fn})

    def start(self):
        pass


def test_scheduler_registers_receipt_miss_and_run_scrubs(param_env, monkeypatch):
    import lantai.core.scheduler as scheduler_mod
    from lantai.core.settings import settings

    fake = _FakeScheduler()
    # start_scheduler() 会重建 _scheduler（:249），必须连 BackgroundScheduler 类一起替换
    # （形态同 tests/test_scheduler.py::fake_scheduler）
    monkeypatch.setattr(scheduler_mod, "BackgroundScheduler", lambda **kw: fake)
    monkeypatch.setattr(scheduler_mod, "_scheduler", fake)
    scheduler_mod.start_scheduler()
    job = next(j for j in fake.jobs if j["id"] == "receipt_miss")
    assert job["trigger"] == "interval"
    assert job["kwargs"].get("minutes") == settings.RECEIPT_MISS_CRON_MINUTES
    assert job["kwargs"].get("replace_existing") is True

    from lantai.workers.digest_worker import run_receipt_miss_scan

    event_id = _log_event()
    _rewind(event_id, param_env)
    scheduler_mod.WORKER_LAST_RUN.pop("receipt_miss", None)
    run_receipt_miss_scan()
    sf, _engine = param_env
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev.receipt_status == "missed"
        assert ev.query_text == "" and ev.query_norm_hash == ""
    assert scheduler_mod.WORKER_LAST_RUN.get("receipt_miss"), "record_run('receipt_miss') 未落"


# ── 红测 D：存量 scrub——pending/missed 抹文、acked/vetoed 零接触、幂等 ────


def _load_scrub():
    spec = importlib.util.spec_from_file_location(
        "scrub_retrieval_query_text", REPO / "scripts" / "scrub_retrieval_query_text.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scrub_pending_missed_only_idempotent(param_env):
    scrub = _load_scrub()
    ids = {
        "pending": _seed_event(param_env, status="pending", query="机密A"),
        "missed": _seed_event(param_env, status="missed", query="机密B"),
        "acked": _seed_event(param_env, status="acked", query="正常C"),
        "vetoed": _seed_event(param_env, status="vetoed", query="机密D"),
    }
    sf, _engine = param_env

    # dry-run：只统计不写库
    stats = scrub.scrub(apply=False)
    assert stats["pending"] >= 1 and stats["missed"] >= 1
    with sf() as s:
        assert s.get(RetrievalEvent, ids["pending"]).query_text == "机密A"

    # apply：pending/missed 抹文，acked/vetoed 零接触
    first = scrub.scrub(apply=True)
    assert first["pending"] >= 1 and first["missed"] >= 1
    with sf() as s:
        for st in ("pending", "missed"):
            ev = s.get(RetrievalEvent, ids[st])
            assert ev.query_text == "" and ev.query_norm_hash == ""
        for st in ("acked", "vetoed"):
            ev = s.get(RetrievalEvent, ids[st])
            assert ev.query_text != "", f"{st} 行不得被 scrub 触碰"

    # 幂等：连跑两遍，第二遍零改动
    second = scrub.scrub(apply=True)
    assert second["pending"] == 0 and second["missed"] == 0, second
    with sf() as s:
        assert s.get(RetrievalEvent, ids["acked"]).query_text == "正常C"


# ── 红测 E：vetoed 结构性隔离——missed 扫描绝不碰 vetoed 行 ─────────────────


def test_missed_scan_never_touches_vetoed(param_env):
    sf, _engine = param_env
    vid = _seed_event(param_env, status="vetoed", query="历史脏数据原文")
    pid = _seed_event(param_env, status="pending", query="超龄pending原文")

    from lantai.observability.retrieval_log import mark_missed_receipts

    mark_missed_receipts(timeout_seconds=60)
    with sf() as s:
        v = s.get(RetrievalEvent, vid)
        assert v.receipt_status == "vetoed"
        assert v.query_text == "历史脏数据原文", "missed 扫描绝不碰 vetoed 行"
        assert s.get(RetrievalEvent, pid).receipt_status == "missed"
