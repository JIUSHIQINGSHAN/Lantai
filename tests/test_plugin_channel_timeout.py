"""票 73：插件层对 serve 子进程读路径零超时——承诺的 5s/30s 硬超时常量从未接线。

`.scratch/v0.68-next-window-2026-11/issues/73-plugin-serve-read-no-timeout.md`

**先说影响**：模块头承诺「单次请求 5 秒硬超时（防卡死）」，但 `_REQUEST_TIMEOUT`
/ `_DIALOGUE_TIMEOUT` 全文件零引用；六个 `_call_*` 通道与 `_wait_ready` 的逐字节
`read(1)` 无墙——serve 子进程「活着但不应答」（prewarm 建连锁竞争/死锁）时首次
`read(1)` 即永久阻塞，且握着 `_lock`，`_ensure_proc` 与其余通道全部跟进冻结；
`pre_llm_call` 是 Hermes 同步回调 → 宿主整轮对话冻死，无异常、无日志、无自愈。

**修法**：`_exchange_line` 看门狗——`threading.Timer` 到期 kill 子进程，阻塞中的
write/read 以 EOF 返回，抛 `TimeoutError`（⊂ OSError）路由进各通道既有 except
分支：静默降级 + `_proc` 置 None，下次 `_ensure_proc` 按 `poll()` 存活判据自动
重启恢复服务。`_wait_ready` 读墙（`_READY_READ_WALL`=60s）独立于探测 deadline：
冷启动加载 chromadb/jieba 10-15s 是合法等待（prewarm 在 stdin 循环之前，探测
阻塞本就是等加载），60s 兜底真挂死。

**测试边界声明**：真实「活着但不应答」子进程（`python -c "import time; sleep(120)"`）
经模块态注入（`_proc`/`_proc_ready`——事件模拟，非 mock 被测逻辑）；看门狗/kill/
EOF/异常复位路径全部真跑。对话与就绪探测的墙宽在测试内收窄（改模块常量，与
idempotency 票 80 的 LEASE_SECONDS 同法）；注入主通道按承诺 5s 原值跑。恢复链
用真 responder 子进程（`LANTAI_SHELL_HOOK` 指向 NDJSON 应答脚本）端到端验证
「超时 kill → 下次调用重启 → 服务恢复」零 mock。
"""

from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"

# 修复前此墙内线程必不返回（红：永久阻塞）；修复后按承诺墙宽（5s/收窄后 1s）
# 返回，墙给足余量只用于红态判活
_WALL = 10.0

# 真 responder：NDJSON 应答协议与 shell_hook --serve 同形（一行请求一行响应），
# 秒答免 prewarm——验证正常路径零漂移与超时后的重启恢复，不加载 chromadb
_RESPONDER_SRC = """\
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    sys.stdout.write(json.dumps({"context": "ok", "event_id": "evt_test"}) + "\\n")
    sys.stdout.flush()
"""


def _hung_child() -> subprocess.Popen:
    """真实「活着但不应答」子进程：不读 stdin、不写 stdout，只睡。"""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
    )


def _call_in_thread(fn, /, *args, **kwargs):
    """在子线程里真调 fn——修复前线程永久阻塞（is_alive 判红），不挂死测试进程。"""
    box: dict = {}

    def _run():
        try:
            box["out"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 —— 收集线程内异常供断言诊断
            box["err"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(_WALL)
    return t, box


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    responder = tmp_path / "responder_hook.py"
    responder.write_text(_RESPONDER_SRC, encoding="utf-8")
    # 环境注入须在模块导入前：resolve_hook_paths 在 import 时读这两个变量
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    spec = importlib.util.spec_from_file_location("remembrance_plugin_timeout", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    yield m
    # 清场：kill 注入/派生的子进程（若有），让阻塞线程以 EOF 退出，不外溢
    proc = m._proc
    if proc is not None:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    m._proc = None
    m._proc_ready = False


class TestCallHookTimeout:
    """关闭条件 2：真插件 `_call_hook` 对挂死子进程按承诺 5s 墙返回 None 并复位。"""

    def test_hook_returns_within_promised_wall(self, mod):
        proc = _hung_child()
        mod._proc = proc
        mod._proc_ready = True  # 注入点：就绪态直测请求读墙（跳过冷启动探测）
        t0 = time.monotonic()
        t, box = _call_in_thread(mod._call_hook, "测试注入查询")
        assert not t.is_alive(), "修复前 _call_hook 超出承诺墙宽仍永久阻塞（红）"
        assert "err" not in box, f"超时应走既有异常路径静默降级，不该外抛：{box}"
        assert box.get("out") is None, f"超时应返回 None（宁 miss）：{box}"
        elapsed = time.monotonic() - t0
        assert elapsed < mod._REQUEST_TIMEOUT + 3.0, f"承诺 5s 墙，实测 {elapsed:.1f}s"
        assert proc.poll() is not None, "超时后子进程应被看门狗 kill"
        assert mod._proc is None and mod._proc_ready is False, (
            "超时应经既有异常路径复位 _proc/_proc_ready，触发下次重启"
        )


class TestDialogueChannelTimeout:
    """对话写通道同病：30s 承诺从未接线（测试内收窄到 1s 验证同一路径）。"""

    def test_dialogue_returns_within_wall(self, mod):
        proc = _hung_child()
        mod._proc = proc
        mod._proc_ready = True
        mod._DIALOGUE_TIMEOUT = 1.0
        t, box = _call_in_thread(
            mod._call_dialogue, "记住：明天下午3点开会", session_id="sess_1", turn=1
        )
        assert not t.is_alive(), "修复前 _call_dialogue 永久阻塞（红）"
        assert "err" not in box, f"超时应静默降级，不该外抛：{box}"
        assert box.get("out") is None
        assert proc.poll() is not None, "超时后子进程应被 kill"
        assert mod._proc is None, "超时应复位 _proc"


class TestOtherChannelsTimeout:
    """其余四通道（回执/否决/底本注入/底本写入）读路径同墙——逐一真挂死验证。"""

    @pytest.mark.parametrize(
        "call,kwargs",
        [
            ("_call_backfill", {"event_id": "evt_1", "used_ids": ["mem_1"]}),
            ("_call_veto", {"event_id": "evt_1"}),
            ("_call_checkpoint", {}),
            ("_call_checkpoint_write", {"session_id": "sess_1", "blocks": {"摘要": "内容"}}),
        ],
        ids=["backfill", "veto", "checkpoint", "checkpoint_write"],
    )
    def test_channel_times_out_and_resets(self, mod, call, kwargs):
        proc = _hung_child()
        mod._proc = proc
        mod._proc_ready = True
        mod._REQUEST_TIMEOUT = 1.0
        mod._DIALOGUE_TIMEOUT = 1.0
        t, box = _call_in_thread(getattr(mod, call), **kwargs)
        assert not t.is_alive(), f"修复前 {call} 永久阻塞（红）"
        assert "err" not in box, f"超时应静默降级，不该外抛：{box}"
        assert box.get("out") is None, f"{call} 超时应返回 None：{box}"
        assert proc.poll() is not None, "超时后子进程应被 kill"
        assert mod._proc is None, "超时应复位 _proc"


class TestWaitReadyHang:
    """就绪探测同病：15s deadline 只挡迭代之间，首次 read(1) 无墙（测试内收窄）。"""

    def test_wait_ready_bounded_by_read_wall(self, mod):
        proc = _hung_child()
        mod._proc = proc
        mod._proc_ready = False
        mod._READY_READ_WALL = 1.0
        t, box = _call_in_thread(mod._wait_ready, 15.0)
        assert not t.is_alive(), "修复前 _wait_ready 探测读永久阻塞（红）"
        assert box.get("out") is False, f"挂死子进程应判未就绪：{box}"
        assert proc.poll() is not None, "读墙到期应 kill 子进程"


class TestRecoveryAfterTimeout:
    """关闭条件 2/3 端到端：正常应答零漂移 → 挂死超时 → 下次调用重启恢复服务。

    真 responder 子进程（LANTAI_SHELL_HOOK 指向 NDJSON 应答脚本）零 mock：
    超时 kill 后 _call_hook 内部的 _ensure_proc 按 poll() 判据重启新子进程，
    _wait_ready 重探，注入恢复。
    """

    def test_timeout_then_next_call_recovers(self, mod):
        # 1) 正常路径：真 responder 应答，注入零漂移
        proc1 = mod._ensure_proc()
        assert proc1 is not None and proc1.poll() is None, "responder 子进程应存活"
        assert mod._wait_ready() is True, "responder 应通过就绪探测"
        out = mod._call_hook("正常注入查询")
        assert isinstance(out, dict) and out.get("context") == "ok", f"正常注入漂移：{out}"

        # 2) 换上挂死子进程 → 按承诺墙超时静默降级（先杀 responder 防孤儿）
        proc1.kill()
        hung = _hung_child()
        mod._proc = hung
        mod._proc_ready = True
        t, box = _call_in_thread(mod._call_hook, "挂死注入查询")
        assert not t.is_alive(), "修复前 _call_hook 永久阻塞（红）"
        assert box.get("out") is None, f"超时应返回 None：{box}"
        assert hung.poll() is not None, "超时后挂死子进程应被 kill"

        # 3) 后续调用：_ensure_proc 重启 responder → 服务恢复（关闭条件 2 收尾）
        out = mod._call_hook("恢复后注入查询")
        assert isinstance(out, dict) and out.get("context") == "ok", f"超时后未恢复：{out}"
        assert mod._proc is not None and mod._proc.poll() is None
        assert mod._proc.pid != hung.pid, "恢复后应为新子进程"
