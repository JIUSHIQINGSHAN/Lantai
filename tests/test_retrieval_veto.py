"""否决轮回执收口（v0.67 票 01 / ADR-0084）服务端冒烟测试。

不 mock：真实内存库（param_env 同款）落 RetrievalEvent；`mark_vetoed` 状态机
（pending→vetoed）、missed 扫描越权、可追溯报告分列、backfill 终态保护全部
直调真实实现——被否决的一轮从未发给模型，DLP 拦下的原话不得留在检索日志。
"""

import pytest


@pytest.fixture()
def veto_env(param_env):
    return param_env


def _log_event(query="我的银行卡密码是 864213，帮我记一下", session_id="s-veto"):
    from lantai.observability.retrieval_log import log_retrieval

    return log_retrieval(
        query,
        [{"score": 0.9, "memory": {"id": "m1"}}],
        latency_ms=5,
        session_id=session_id,
        request_id="req-veto",
    )


class TestMarkVetoed:
    def test_scrubs_query_and_used_ids(self, veto_env):
        """veto 抹原文：query_text / query_norm_hash 清空、used_ids 清空、置 vetoed 终态。

        DLP 拦的是低熵秘密（密码/密钥），归一化哈希对低熵原文就是可验证口令，
        留着等于没抹；行本身保留（vetoed 单列计数可观测）。
        """
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import mark_vetoed

        event_id = _log_event()
        sf, _engine = veto_env

        assert mark_vetoed(event_id) is True

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.receipt_at is not None
            assert ev.query_text == ""
            assert ev.query_norm_hash == ""
            assert ev.used_ids == []
            # 无原文元数据不动（票据口径）：召回了什么、延迟多少照旧可观测
            assert ev.result_ids == ["m1"]
            assert ev.latency_ms == 5
            assert ev.session_id == "s-veto"

    def test_idempotent_second_call_returns_false(self, veto_env):
        """幂等：已 vetoed 再调无新状态转移，返回 False 且不再改写。"""
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import mark_vetoed

        event_id = _log_event()
        sf, _engine = veto_env

        assert mark_vetoed(event_id) is True
        first_receipt_at = None
        with sf() as s:
            first_receipt_at = s.get(RetrievalEvent, event_id).receipt_at

        assert mark_vetoed(event_id) is False
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.receipt_at == first_receipt_at
            assert ev.query_text == ""

    def test_missing_event_returns_false(self, veto_env):
        """缺失 id：返回 False，不炸不建行。"""
        from lantai.observability.retrieval_log import mark_vetoed

        assert mark_vetoed("rev-does-not-exist") is False
        assert mark_vetoed("") is False

    def test_acked_can_still_be_vetoed(self, veto_env):
        """已回执的事件也可 veto（守门入口不预设时序）：回执被作废、原文照抹。"""
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import backfill_used_ids, mark_vetoed

        event_id = _log_event()
        backfill_used_ids(event_id, ["m1"])
        assert mark_vetoed(event_id) is True

        sf, _engine = veto_env
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.used_ids == []
            assert ev.query_text == ""


class TestVetoedIsTerminal:
    def test_missed_scan_does_not_touch_vetoed(self, veto_env):
        """mark_missed_receipts 只认 pending：vetoed 不被超龄扫描改写成 missed。"""
        from datetime import timedelta

        from lantai.core.time import utcnow
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import mark_missed_receipts, mark_vetoed

        event_id = _log_event()
        mark_vetoed(event_id)
        sf, _engine = veto_env
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            ev.created_at = utcnow() - timedelta(minutes=10)
            s.add(ev)
            s.commit()

        assert mark_missed_receipts(timeout_seconds=300) == 0
        with sf() as s:
            assert s.get(RetrievalEvent, event_id).receipt_status == "vetoed"

    def test_backfill_does_not_revive_vetoed(self, veto_env):
        """vetoed 是终态：迟到的 backfill 不把它拖回 acked、不回写保活计数。"""
        from lantai.models.tables import MemoryItem, RetrievalEvent
        from lantai.observability.retrieval_log import backfill_used_ids, mark_vetoed

        event_id = _log_event()
        mark_vetoed(event_id)
        sf, _engine = veto_env
        with sf() as s:
            s.add(MemoryItem(id="m1", content="content-m1", status="active"))
            s.commit()

        backfill_used_ids(event_id, ["m1"])

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.used_ids == []
            assert s.get(MemoryItem, "m1").injection_count == 0


class TestTraceabilityReportVetoed:
    def test_report_counts_vetoed_separately(self, veto_env):
        """报告补 vetoed 计数：单列，不入 acked/pending 分子。"""
        from lantai.observability.retrieval_log import mark_vetoed, receipt_traceability_report

        vetoed_id = _log_event(session_id="s-veto-a")
        mark_vetoed(vetoed_id)
        _log_event(session_id="s-veto-b")  # 留一个 pending 对照

        report = receipt_traceability_report()
        assert report["vetoed"] >= 1
        assert report["pending"] >= 1
        assert report["acked"] == 0
        assert report["traceability_rate"] is None
