"""巡检 2026-11-16 12:00：插件超墙 kill 后不收尸，「超时返回 ⇒ 子进程已退出」只是大概率成立。

**先说影响**：票 73 的 watchdog 在墙到期时 kill serve 子进程，阻塞中的 read(1) 以 EOF
返回，调用方走降级。但 SIGKILL 下内核是先关 fd（EOF 已到）、后转僵尸，两步之间有窗口；
旧实现从不 wait，returncode 要等下一次有人 poll() 才会设上，紧接着的 poll() 可能仍得
None。CI 上 `test_channel_times_out_and_resets[backfill]` 就是这样红的（run 43769589926：
「超时后子进程应被 kill / assert None is not None」）。产品侧同一窗口里的 _ensure_proc
会误判旧进程还活着、往断管里再写一次才重启。

**修法**：`_request_line` 的 watchdog 先立旗再 kill；finally 里见旗就 `proc.wait(≤_REAP_TIMEOUT)`
收尸。正常应答路径（墙未到）不碰 wait。

**断言只看 `returncode` 属性、不调 poll()**：poll() 自己会顺手收尸，会把旧实现的缺口补上
而盖住红态。旧实现下无人 wait/poll，returncode 必为 None（send_signal 内的 poll 在 kill
之前），所以本文件对旧实现是确定红，不靠赌竞态。
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"
_WALL = 10.0  # 线程判活余量，只用于防挂死测试进程

_RESPONDER_SRC = """\
import json, sys
for line in sys.stdin:
    if line.strip():
        sys.stdout.write(json.dumps({"context": "ok", "event_id": "evt_test"}) + "\\n")
        sys.stdout.flush()
"""


def _hung_child() -> subprocess.Popen:
    """活着但不应答：不读 stdin、不写 stdout。"""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _responder(tmp_path: Path) -> subprocess.Popen:
    src = tmp_path / "responder.py"
    src.write_text(_RESPONDER_SRC, encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(src)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _in_thread(fn, /, *args, **kwargs):
    box: dict = {}

    def _run():
        try:
            box["out"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            box["err"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(_WALL)
    return t, box


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    responder = tmp_path / "responder_hook.py"
    responder.write_text(_RESPONDER_SRC, encoding="utf-8")
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    spec = importlib.util.spec_from_file_location("remembrance_plugin_reap", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    m._REQUEST_TIMEOUT = 1.0
    m._DIALOGUE_TIMEOUT = 1.0
    spawned: list[subprocess.Popen] = []
    m._test_spawned = spawned
    yield m
    for p in spawned + ([m._proc] if m._proc is not None else []):
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)


_CHANNELS = [
    ("_call_hook", ("挂死注入查询",), {}),
    ("_call_dialogue", ("记住：明天下午3点开会",), {"session_id": "s1", "turn": 1}),
    ("_call_backfill", (), {"event_id": "evt_1", "used_ids": ["mem_1"]}),
    ("_call_veto", (), {"event_id": "evt_1"}),
    ("_call_checkpoint", (), {}),
    ("_call_checkpoint_write", (), {"session_id": "s1", "blocks": {"摘要": "内容"}}),
]


class TestReapedOnTimeout:
    @pytest.mark.parametrize("call,args,kwargs", _CHANNELS, ids=[c[0] for c in _CHANNELS])
    def test_channel_returns_with_child_reaped(self, mod, call, args, kwargs):
        """六个通道：超时返回的那一刻子进程已被收尸（returncode 已设，不靠事后 poll）。"""
        proc = _hung_child()
        mod._test_spawned.append(proc)
        mod._proc = proc
        mod._proc_ready = True
        t, box = _in_thread(getattr(mod, call), *args, **kwargs)
        assert not t.is_alive(), f"{call} 超墙未返回"
        assert "err" not in box, box
        assert proc.returncode is not None, (
            f"{call} 超时返回时子进程未收尸：紧接着的 poll() 可能仍得 None（CI 偶发红的根因）"
        )

    def test_wait_ready_hung_child_reaped(self, mod):
        """就绪探测同墙：探测超时返回 False 时子进程已收尸。"""
        proc = _hung_child()
        mod._test_spawned.append(proc)
        mod._proc = proc
        mod._proc_ready = False
        t, box = _in_thread(mod._wait_ready, 1.0)
        assert not t.is_alive()
        assert box.get("out") is False, box
        assert proc.returncode is not None

    def test_request_line_timeout_bounded_and_reaped(self, mod):
        """底层原语：空帧返回、已收尸，耗时不超过 墙 + 收尸上限 + 余量。"""
        proc = _hung_child()
        mod._test_spawned.append(proc)
        t0 = time.monotonic()
        buf = mod._request_line(proc, b'{"query":"x"}\n', 1.0)
        elapsed = time.monotonic() - t0
        assert bytes(buf) == b""
        assert proc.returncode is not None
        assert elapsed < 1.0 + mod._REAP_TIMEOUT + 2.0, elapsed

    def test_repeated_timeouts_always_reaped(self, mod):
        """压竞态：连续 15 次超时，每次返回时都已收尸（旧实现每次都红）。"""
        misses = 0
        for _ in range(15):
            proc = _hung_child()
            mod._test_spawned.append(proc)
            mod._request_line(proc, b'{"query":"x"}\n', 0.2)
            if proc.returncode is None:
                misses += 1
        assert misses == 0, f"15 次超时里有 {misses} 次返回时子进程未收尸"


class TestControls:
    def test_normal_response_does_not_kill_or_wait(self, mod, tmp_path, monkeypatch):
        """对照臂：墙内应答的路径零漂移——不 kill、不 wait，子进程照活。"""
        proc = _responder(tmp_path)
        mod._test_spawned.append(proc)
        waits: list = []
        real_wait = proc.wait
        monkeypatch.setattr(
            proc, "wait", lambda *a, **k: waits.append((a, k)) or real_wait(*a, **k)
        )
        buf = mod._request_line(proc, b'{"query":"x"}\n', 5.0)
        assert b'"context": "ok"' in bytes(buf)
        assert waits == [], "正常路径不该碰 wait"
        assert proc.returncode is None and proc.poll() is None, "正常应答后子进程应照活"
        buf2 = mod._request_line(proc, b'{"query":"y"}\n', 5.0)
        assert b'"context": "ok"' in bytes(buf2), "同一子进程应能接着应答"

    def test_reap_gives_up_after_cap_without_raising(self, mod, caplog):
        """kill 后迟迟不退出（D 状态等极端情形）：收尸最多等 _REAP_TIMEOUT，不外抛、不无限挂住宿主。"""

        class _Stuck:
            pid = 4242
            seen: list = []

            def wait(self, timeout=None):
                self.seen.append(timeout)
                raise subprocess.TimeoutExpired("x", timeout)

        stuck = _Stuck()
        with caplog.at_level(logging.WARNING):
            mod._reap_after_kill(stuck)
        assert stuck.seen == [mod._REAP_TIMEOUT]
        assert mod._REAP_TIMEOUT <= 2.0
        assert any("仍未退出" in r.getMessage() for r in caplog.records)

    def test_recovery_after_timeout_still_restarts(self, mod):
        """自愈闭环不变：超时后下一次调用由 _ensure_proc 重启 responder 并拿到合法帧。"""
        hung = _hung_child()
        mod._test_spawned.append(hung)
        mod._proc = hung
        mod._proc_ready = True
        t, box = _in_thread(mod._call_hook, "挂死")
        assert not t.is_alive() and box.get("out") is None, box
        out = mod._call_hook("恢复后注入查询")
        assert isinstance(out, dict) and out.get("context") == "ok", out
        assert mod._proc is not None and mod._proc.pid != hung.pid
