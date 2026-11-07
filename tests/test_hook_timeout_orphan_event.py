"""硬墙超时后的孤儿检索事件不得带原话落库（巡检 2026-11-07 12:00）。

背景（ADR-0084 / v0.67 票 01 + v0.68 票 01）：被宿主否决的一轮，插件靠检索响应里的
`event_id` 发 retrieval_veto 帧，服务端 `mark_vetoed` 抹掉 `query_text` /
`query_norm_hash`。零命中轮补上 event_id 之后，还剩一条够不着的路：

`_handle_one` 的 QUERY 分支把 `build_context` 放进 `_run_with_timeout`，硬墙
（`SHELL_HOOK_TIMEOUT`，默认 2s）一到主线程就返回画像兜底——**不带 event_id**；
而 daemon worker 线程不可取消，跑完照样 `_try_log`，把原话写进 RetrievalEvent。
宿主手里没有这个 id：既回执不了（永远 pending→missed），这一轮被 DLP 否决时
也否决不了——密码原文永久留在检索日志里。

修法：worker 跑完时若主线程已经放弃等待（abandoned），就地把它自己落的那条事件
抹掉原话、置 missed（`mark_abandoned`）。完成与放弃在同一把锁下二选一，不留竞态窗口。

不 mock 库：真实内存库（param_env）+ shell_hook 真实 `_handle_one`；只把
`hybrid_search` 换成睡过硬墙的替身，模拟冷启动/远端 embed 慢。
"""

import json
import time

import pytest

SECRET_QUERY = "我的银行卡密码是 864213，帮我记一下"


@pytest.fixture()
def orphan_env(param_env):
    return param_env


def _load_hook_module(monkeypatch, name: str):
    """同 test_retrieval_veto.py 的加载方式（scripts/ 不是包）。"""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(name, Path("scripts/shell_hook.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
    return mod


def _slow_search(delay: float, results: list | None = None):
    def _fake(query, top_k=5, budget_s=None, **_kw):
        time.sleep(delay)
        return list(results or [])

    return _fake


def _events(sf):
    from sqlmodel import select

    from lantai.models.tables import RetrievalEvent

    with sf() as s:
        return list(s.exec(select(RetrievalEvent)).all())


def _wait_for_event(sf, timeout: float = 5.0):
    """等 worker 线程把事件落库并（修复后）收尾。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _events(sf)
        if rows and all(r.receipt_status != "pending" for r in rows):
            return rows
        time.sleep(0.05)
    return _events(sf)


class TestTimeoutOrphanEvent:
    def test_timed_out_zero_hit_leaves_no_secret(self, orphan_env, monkeypatch):
        """零命中 + 超时：响应无 event_id，worker 迟到落的事件必须抹掉原话、置 missed。"""
        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_zero")
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 1)
        monkeypatch.setattr(mod, "hybrid_search", _slow_search(1.4))
        sf, _engine = orphan_env

        out = mod._handle_one(
            json.dumps({"type": "query", "query": SECRET_QUERY, "session_id": "s-orphan"})
        )
        # 前提：确实走了超时兜底——宿主拿不到 event_id，否决链够不着
        assert "event_id" not in out, out

        rows = _wait_for_event(sf)
        assert len(rows) == 1, rows
        ev = rows[0]
        assert "864213" not in (ev.query_text or "")
        assert ev.query_text == ""
        assert ev.query_norm_hash == ""
        assert ev.receipt_status == "missed"
        assert ev.receipt_at is not None
        # 无原文元数据照留：超时轮的延迟仍可观测
        assert ev.latency_ms >= 1000
        assert ev.session_id == "s-orphan"

    def test_timed_out_hit_path_leaves_no_secret(self, orphan_env, monkeypatch):
        """命中 + 超时：同上，命中路径落的事件也要收尾（result_ids 保留）。"""
        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_hit")
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 1)
        hit = {
            "score": 0.9,
            "score_mode": "reranker",
            "memory": {
                "id": "mem-orphan",
                "content": "工资卡是招商银行尾号 0423 的那张。",
                "lane": "fact",
                "status": "active",
            },
        }
        monkeypatch.setattr(mod, "hybrid_search", _slow_search(1.4, [hit]))
        sf, _engine = orphan_env

        out = mod._handle_one(
            json.dumps({"type": "query", "query": SECRET_QUERY, "session_id": "s-orphan-hit"})
        )
        assert "event_id" not in out, out

        rows = _wait_for_event(sf)
        assert len(rows) == 1, rows
        ev = rows[0]
        assert ev.query_text == ""
        assert ev.query_norm_hash == ""
        assert ev.receipt_status == "missed"
        assert ev.result_ids == ["mem-orphan"]

    def test_in_time_response_unchanged(self, orphan_env, monkeypatch):
        """对照臂：墙内返回的轮次照旧带 event_id，事件保持 pending、原话可被 veto 抹。"""
        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_ctrl")
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 30)
        monkeypatch.setattr(mod, "hybrid_search", _slow_search(0.0))
        sf, _engine = orphan_env

        out = mod._handle_one(
            json.dumps({"type": "query", "query": SECRET_QUERY, "session_id": "s-orphan-ctrl"})
        )
        event_id = out.get("event_id")
        assert event_id, out
        rows = _events(sf)
        assert len(rows) == 1
        assert rows[0].receipt_status == "pending"
        assert rows[0].query_text == SECRET_QUERY

        vout = mod._handle_one(json.dumps({"type": "retrieval_veto", "event_id": event_id}))
        assert vout.get("vetoed") is True


class TestMarkAbandoned:
    def _log(self):
        from lantai.observability.retrieval_log import log_retrieval

        return log_retrieval(
            SECRET_QUERY,
            [{"score": 0.9, "memory": {"id": "m1"}}],
            latency_ms=2100,
            session_id="s-ab",
        )

    def test_scrubs_and_marks_missed(self, orphan_env):
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import mark_abandoned

        event_id = self._log()
        sf, _engine = orphan_env
        assert mark_abandoned(event_id) is True
        with sf() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.query_text == ""
            assert ev.query_norm_hash == ""
            assert ev.used_ids == []
            assert ev.receipt_status == "missed"
            assert ev.receipt_at is not None
            assert ev.result_ids == ["m1"]
            assert ev.latency_ms == 2100

    def test_does_not_touch_vetoed_or_acked(self, orphan_env):
        """只动 pending：vetoed 终态不改成 missed；已 acked 的不被降级。"""
        from lantai.models.tables import RetrievalEvent
        from lantai.observability.retrieval_log import (
            backfill_used_ids,
            mark_abandoned,
            mark_vetoed,
        )

        sf, _engine = orphan_env
        vetoed_id = self._log()
        assert mark_vetoed(vetoed_id) is True
        assert mark_abandoned(vetoed_id) is False

        acked_id = self._log()
        backfill_used_ids(acked_id, ["m1"])
        assert mark_abandoned(acked_id) is False
        with sf() as s:
            assert s.get(RetrievalEvent, vetoed_id).receipt_status == "vetoed"
            acked = s.get(RetrievalEvent, acked_id)
            assert acked.receipt_status == "acked"
            assert acked.query_text == SECRET_QUERY

    def test_missing_or_empty_id(self, orphan_env):
        from lantai.observability.retrieval_log import mark_abandoned

        assert mark_abandoned("rev-none") is False
        assert mark_abandoned("") is False


class TestRunWithTimeoutHandoff:
    """`_run_with_timeout` 的完成/放弃二选一：不依赖库，直接测回调时机。"""

    def test_on_abandon_called_with_late_result(self, monkeypatch):
        import threading

        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_rwt")
        seen: list = []
        done = threading.Event()

        def slow():
            time.sleep(0.3)
            return {"event_id": "rev-late"}

        def on_abandon(result):
            seen.append(result)
            done.set()

        out = mod._run_with_timeout(
            slow, 0.05, _fallback=lambda: {"context": "persona"}, _on_abandon=on_abandon
        )
        assert out == {"context": "persona"}
        assert done.wait(3.0)
        assert seen == [{"event_id": "rev-late"}]

    def test_on_abandon_not_called_when_in_time(self, monkeypatch):
        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_rwt2")
        seen: list = []
        out = mod._run_with_timeout(
            lambda: {"event_id": "rev-ok"}, 5, _on_abandon=lambda r: seen.append(r)
        )
        assert out == {"event_id": "rev-ok"}
        time.sleep(0.05)
        assert seen == []

    def test_on_abandon_failure_is_swallowed(self, monkeypatch):
        """收尾回调自身抛错：只记日志，不炸 worker 线程（serve 循环不受影响）。"""
        import threading

        mod = _load_hook_module(monkeypatch, "shell_hook_orphan_rwt3")
        called = threading.Event()

        def boom(_result):
            called.set()
            raise RuntimeError("db down")

        out = mod._run_with_timeout(
            lambda: (time.sleep(0.2), {"event_id": "x"})[1], 0.05, _on_abandon=boom
        )
        assert out == {}
        assert called.wait(3.0)
