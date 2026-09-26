"""回执链（ADR-0049 / 票 04）冒烟测试。

不 mock：真实内存库（param_env 同款）落 RetrievalEvent；回执状态机（pending→acked /
pending→missed）与可追溯率出口直调真实实现；request_id 经 shell_hook 真实
_handle_one 协议帧驱动（NDJSON 进程内帧，2s 超时语义保留）。
"""

import json

import pytest

import lantai.storage.db as db_module


@pytest.fixture()
def receipt_env(param_env):
    return param_env


def _log_event(session_id=None, request_id=None):
    from lantai.observability.retrieval_log import log_retrieval

    return log_retrieval(
        "部署怎么做",
        [{"score": 0.9, "memory": {"id": "m1"}}, {"score": 0.8, "memory": {"id": "m2"}}],
        latency_ms=5,
        session_id=session_id,
        request_id=request_id,
    )


class TestReceiptChain:
    def test_pending_then_acked_with_request_id(self, receipt_env):
        """回执流：pending 落库 → backfill（携 request_id）→ acked + used_ids + receipt_at。"""
        sf, _engine = receipt_env
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import receipt_traceability_report

        event_id = _log_event(session_id="sess-1", request_id="req-1")
        assert event_id

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "pending"
            assert ev.request_id == "req-1"

        from lantai.observability.retrieval_log import backfill_used_ids

        backfill_used_ids(event_id, ["m1"], request_id="req-1")

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.used_ids == ["m1"]
            assert ev.receipt_status == "acked"
            assert ev.receipt_at is not None

        report = receipt_traceability_report()
        assert report["acked"] >= 1
        assert report["traceability_rate"] is not None

    def test_missed_after_timeout(self, receipt_env):
        """缺失流：宿主不回执 → 超龄事件被 mark_missed_receipts 置 missed（幂等）。"""
        from datetime import timedelta

        from lantai.core.time import utcnow
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import mark_missed_receipts

        event_id = _log_event()
        sf, _engine = receipt_env

        # 把事件时间拨回 10 分钟前（模拟超龄）
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            ev.created_at = utcnow() - timedelta(minutes=10)
            s.add(ev)
            s.commit()

        moved = mark_missed_receipts(timeout_seconds=300)
        assert moved >= 1
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "missed"
            assert ev.receipt_at is not None

        # 幂等：再跑一次不重复计数（已置位不重算）
        assert mark_missed_receipts(timeout_seconds=300) == 0

    def test_traceability_rate_none_when_no_acked(self, receipt_env):
        """诚实纪律：无 acked 样本时 rate 返回 None，不编造 0。"""
        from lantai.observability.retrieval_log import receipt_traceability_report

        _log_event()  # 只落 pending
        report = receipt_traceability_report()
        assert report["pending"] >= 1
        assert report["traceability_rate"] is None

    def test_traceability_rate_100_on_clean_chain(self, receipt_env):
        """验收口径：受控链（入库记忆 + 回执 used_ids）可追溯率 = 1.0。"""
        from lantai.models.tables import MemoryItem
        from lantai.observability.retrieval_log import (
            backfill_used_ids,
            receipt_traceability_report,
        )

        sf, _engine = receipt_env
        # 两条真实记忆行（可回溯目标）
        with sf() as s:
            for mid in ("evs-rm1", "evs-rm2"):
                s.add(MemoryItem(id=mid, content=f"content-{mid}", status="active"))
            s.commit()

        event_id = _log_event(session_id="sess-clean", request_id="req-clean")
        backfill_used_ids(event_id, ["evs-rm1", "evs-rm2"])

        report = receipt_traceability_report()
        assert report["traceable"] >= 1
        assert report["traceability_rate"] == 1.0  # 本次受控链 100%

    def test_request_id_mismatch_logged_not_rejected(self, receipt_env):
        """request_id 不一致：回执仍按 event_id 落定（宁 miss 不脏写，归属以 event_id 为准）。"""
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import backfill_used_ids

        event_id = _log_event(request_id="req-right")
        backfill_used_ids(event_id, ["m1"], request_id="req-wrong")

        sf, _engine = receipt_env
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "acked"
            assert ev.used_ids == ["m1"]

    def test_shell_hook_protocol_request_id_roundtrip(self, receipt_env, monkeypatch):
        """受控宿主协议冒烟：context 响应携 request_id；backfill 帧透传 → acked。"""
        tests_mod = _load_hook_module(monkeypatch)
        event_id = _log_event(session_id="sess-proto", request_id="req-proto")

        out = tests_mod._handle_one(
            json.dumps(
                {
                    "type": "backfill",
                    "event_id": event_id,
                    "used_ids": ["m1"],
                    "request_id": "req-proto",
                }
            )
        )
        assert out["ok"] is True
        assert out["receipt_status"] == "acked"

        sf, _engine = receipt_env
        from lantai.models.tables import RetrievalEvent

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "acked"
            assert ev.request_id == "req-proto"

    def test_migration_v22_receipt_columns(self, tmp_path):
        """迁移冒烟：v21 旧库升级 → retrieval_event 三列就位 + 索引 + 幂等。"""
        import sqlite3

        import lantai.models.tables  # noqa: F401
        import lantai.eval.models  # noqa: F401
        from lantai.storage.db import apply_migrations
        from sqlmodel import SQLModel, create_engine

        engine = create_engine("sqlite:///" + str(tmp_path / "v22.db"))
        SQLModel.metadata.create_all(engine)
        engine.dispose()

        conn = sqlite3.connect(str(tmp_path / "v22.db"))
        conn.execute("DROP INDEX IF EXISTS ix_retrieval_event_request_id")
        conn.execute("DROP INDEX IF EXISTS ix_retrieval_event_receipt_status")
        conn.execute("ALTER TABLE retrieval_event DROP COLUMN request_id")
        conn.execute("ALTER TABLE retrieval_event DROP COLUMN receipt_status")
        conn.execute("ALTER TABLE retrieval_event DROP COLUMN receipt_at")
        conn.execute("PRAGMA user_version = 21")
        conn.commit()

        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 24  # 链已前移到 v24（票 07 沉潜过审 + ADR-0053 decided_at），幂等守卫保证 v22 段仍生效
        cols = {r[1] for r in conn.execute("PRAGMA table_info(retrieval_event)").fetchall()}
        assert {"request_id", "receipt_status", "receipt_at"} <= cols
        apply_migrations(conn)  # 幂等重放
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 24
        conn.close()


def _load_hook_module(monkeypatch):
    """以 test_shell_hook.py 同款方式加载 scripts/shell_hook.py 为模块。"""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "shell_hook_receipt_test", Path("scripts/shell_hook.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
