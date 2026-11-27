"""Real MCP guard initialization and concurrent half-open probes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

from lantai.cli import mcp


def test_concurrent_guard_initialization_returns_one_process_instance(monkeypatch):
    with monkeypatch.context() as mp:
        mp.setattr(mcp, "_guard_instance", None)
        start = Barrier(8)

        def initialize():
            start.wait(timeout=10)
            return mcp._get_guard()

        with ThreadPoolExecutor(max_workers=8) as executor:
            guards = list(executor.map(lambda _: initialize(), range(8)))

        assert all(guard is guards[0] for guard in guards), (
            "并发冷启动创建了多个守卫，失败计数和断路状态被分散"
        )
        assert mcp._get_guard() is guards[0]
        assert mcp._guard_instance is guards[0]


def test_concurrent_half_open_calls_admit_one_probe_and_recover():
    clock = [1000.0]
    guard = mcp.LoopGuard(threshold=2, cooldown_s=5, clock=lambda: clock[0])
    for _ in range(2):
        token, rejection = guard.begin("same-operation")
        assert rejection is None
        guard.finish(token, failed=True)
    clock[0] += 6
    start = Barrier(8)
    release = Event()
    rejected = Event()
    counter_lock = Lock()
    rejected_count = 0

    def attempt():
        nonlocal rejected_count
        start.wait(timeout=10)
        token, rejection = guard.begin("same-operation")
        if token is not None:
            assert release.wait(timeout=10)
            guard.finish(token, failed=False)
        else:
            with counter_lock:
                rejected_count += 1
                if rejected_count == 7:
                    rejected.set()
        return token, rejection

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(attempt) for _ in range(8)]
        try:
            assert rejected.wait(timeout=10)
            token, rejection = guard.begin("same-operation")
            assert token is None and rejection["code"] == -32005
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]

    assert sum(token is not None for token, _ in results) == 1
    assert all(rejection["code"] == -32005 for _, rejection in results if rejection)
    token, rejection = guard.begin("same-operation")
    assert token is not None and rejection is None
    guard.finish(token, failed=False)
