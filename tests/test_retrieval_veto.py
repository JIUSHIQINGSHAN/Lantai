"""否决轮回执收口（v0.67 票 01 / ADR-0084）服务端冒烟测试。

不 mock：真实内存库（param_env 同款）落 RetrievalEvent；`mark_vetoed` 状态机
（pending→vetoed）、missed 扫描越权、可追溯报告分列、backfill 终态保护全部
直调真实实现——被否决的一轮从未发给模型，DLP 拦下的原话不得留在检索日志。
协议分发冒烟走 shell_hook 真实 `_handle_one`（NDJSON 进程内帧）。
"""

import json

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


class TestZeroHitVetoReachable:
    """v0.68 票 01：零命中轮的检索事件也必须够得着 veto（响应契约补 event_id）。

    病灶：零命中路径（shell_hook build_context `if not results:`）落库后经
    `_persona_only()` 返回，event_id 被丢——DLP 否决零命中轮时插件构造不出
    retrieval_veto 帧，`mark_vetoed` 永远够不着，原话永久留在
    query_text/query_norm_hash。对照臂：REST routes_search 与 MCP handle_search
    的同名零命中分支本就带 event_id，本票把 shell_hook 对齐到同一口径。
    """

    def test_zero_hit_response_carries_event_id_and_veto_scrubs(self, veto_env, monkeypatch):
        """空库 + 带秘密查询：响应带 event_id → veto 帧抹原话、置 vetoed 终态。"""
        mod = _load_hook_module(monkeypatch)
        # 验证语义 ≠ 验证墙宽（watch/build-context-2s-timeout-flaky）：放宽硬墙，
        # 防全量后段擦线超时走降级把断点遮成假绿；monkeypatch 自动恢复。
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 30)

        out = mod._handle_one(
            json.dumps(
                {
                    "type": "query",
                    "query": "我的银行卡密码是 864213，帮我记一下",
                    "session_id": "s-zero-hit",
                }
            )
        )
        # 红点：零命中响应必须带已落库事件的 event_id（自指词「我的」自然过
        # relevance gate，不 mock gate；空库必零命中）
        event_id = out.get("event_id")
        assert event_id, out

        vout = mod._handle_one(json.dumps({"type": "retrieval_veto", "event_id": event_id}))
        assert vout.get("ok") is True
        assert vout.get("vetoed") is True

        from lantai.models.tables import RetrievalEvent

        sf, _engine = veto_env
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.query_text == ""
            assert ev.query_norm_hash == ""
            assert ev.used_ids == []  # 零命中本就无 used_ids，veto 不受影响

    def test_hit_path_response_unchanged(self, veto_env, monkeypatch):
        """对照臂：命中路径响应带 event_id + evidence，修复不得改坏命中路径。

        命中走真库 FTS/BM25（种子 + sync_fts，同 test_plugin_short_query_persona
        的实证模式）——不在 param_env 之上二次 patch `get_vector_store`：同一绑定
        两层替身撤销错位会被模块状态护栏点名（票 05 续8，conftest 明文警告的
        手写 fixture 反模式）。
        """
        from lantai.models.tables import MemoryItem
        from lantai.storage.fts import sync_fts

        mod = _load_hook_module(monkeypatch)
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 30)

        sf, _engine = veto_env
        with sf() as s:
            m = MemoryItem(
                id="mem-hit",
                content="青梧仓库盘点截止日是每月 26 号，负责人是老魏。",
                lane="fact",
                tier="longterm",
                status="active",
                importance=0.8,
            )
            s.add(m)
            s.commit()
            sync_fts(s, m.id, m.content)
            s.commit()

        out = mod._handle_one(
            json.dumps(
                {
                    "type": "query",
                    "query": "青梧仓库每月的盘点截止日是哪一天",
                    "session_id": "s-hit-ctrl",
                }
            )
        )
        assert out.get("event_id")
        assert out.get("evidence"), out
        assert out["evidence"][0]["id"] == "mem-hit"


class TestProtocolDispatch:
    """协议帧 → shell_hook 分发 → 真库收口（不 mock，同 test_receipt_chain 冒烟形态）。"""

    def test_dispatch_veto_scrubs_event(self, veto_env, monkeypatch):
        """retrieval_veto 帧：_handle_one 端到端 → 原文抹掉、状态落 vetoed。"""
        mod = _load_hook_module(monkeypatch)
        sf, _engine = veto_env

        event_id = _log_event(session_id="s-proto-veto")
        out = mod._handle_one(json.dumps({"type": "retrieval_veto", "event_id": event_id}))
        assert out.get("ok") is True
        assert out.get("vetoed") is True

        from lantai.models.tables import RetrievalEvent

        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.receipt_status == "vetoed"
            assert ev.query_text == ""
            assert ev.query_norm_hash == ""

    def test_dispatch_veto_invalid_frame_silent(self, veto_env, monkeypatch):
        """无效否决帧：解析层拒绝 → {} 零侵入（不炸 serve 循环）。"""
        mod = _load_hook_module(monkeypatch)
        assert mod._handle_one(json.dumps({"type": "retrieval_veto"})) == {}
        assert mod._handle_one(json.dumps({"type": "retrieval_veto", "event_id": 123})) == {}

    def test_dispatch_veto_missing_event_reports_not_applied(self, veto_env, monkeypatch):
        """event_id 在库里不存在：ok=True 但 vetoed=False（幂等重放/竞态如实上报）。"""
        mod = _load_hook_module(monkeypatch)
        out = mod._handle_one(json.dumps({"type": "retrieval_veto", "event_id": "rev-none"}))
        assert out.get("ok") is True
        assert out.get("vetoed") is False


def _load_hook_module(monkeypatch):
    """以 test_shell_hook.py / test_receipt_chain.py 同款方式加载 scripts/shell_hook.py。"""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "shell_hook_veto_test", Path("scripts/shell_hook.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
    return mod
