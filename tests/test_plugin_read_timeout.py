"""插件 serve 读路径硬超时回归锁（v0.68 票 73）。

病灶：六个 _call_* 在 _lock 内逐字节 proc.stdout.read(1) 直到 \\n/EOF，无任何
上限；_wait_ready 的 15s 截止只在两次阻塞读之间检查，子进程活着但不吐行时第一
次 read(1) 即永久阻塞。_call_hook 是宿主同步回调（pre_llm_call）——一次进程级
wedge（无需恶意输入）即冻结 Hermes 对话，无异常、无日志、无自愈。

纪律：不 mock 被测链路——真实子进程（sleep 的沉默进程 / 即时应答的迷你 serve）、
真管道、真插件全链路写读；超时墙经模块常量注入（patch），默认仍回落 5s/30s 承诺
值。测试自身经限时帮手线程执行，保证修复前（读路径无墙）也能秒级见证 RED 而非挂
死整个测试会话。
"""

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"

# 「活着但永不应答」的子进程：进程健康、管道通畅、就是永远不吐一行
_SILENT_CHILD = [sys.executable, "-c", "import time; time.sleep(120)"]

# 迷你 serve：真子进程真管道，读一行回一行合法 JSON 帧。不拉真 shell_hook
# （chromadb/jieba 冷启动 10-15s）——本文件只关心插件读路径行为，不关心服务端。
_MINI_SERVE = """\
import sys

while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    sys.stdout.buffer.write(
        b'{"context":"[mini-serve]","event_id":"ev_t73","evidence":[]}\\n'
    )
    sys.stdout.buffer.flush()
"""


def _spawn_silent_child() -> subprocess.Popen:
    return subprocess.Popen(
        _SILENT_CHILD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _run_with_wall(fn, wall: float) -> tuple[bool, object]:
    """帮手线程限时执行 fn：返回 (是否在 wall 内完成, 返回值)。

    修复前读路径会永久阻塞——不经限时帮手线程，红测会把整个测试会话挂死。
    线程内异常原样传回主线程抛出（unexpected exception 不许伪装成超时降级）。
    """
    result: dict = {}

    def _runner():
        try:
            result["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - 原样传回主线程裁决
            result["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(wall)
    if "error" in result:
        raise result["error"]
    return (not t.is_alive(), result.get("value"))


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("remembrance_plugin_t73", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    yield m
    # 清理：杀掉测试期间拉起/注入的子进程（含修复前红测没被 kill 的沉默进程），
    # 顺带让仍阻塞在 read(1) 上的帮手线程经 EOF 退出
    proc = m._proc
    if proc is not None and proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class TestReadTimeoutWalls:
    """读路径硬超时墙（票 73 关闭条件 1/2）：超墙必降级，降级后必可自愈。"""

    def test_call_hook_silent_child_returns_within_wall(self, mod):
        """注入请求（承诺 5s）：沉默子进程下 _call_hook 必须在墙内返回 None，
        且子进程被 kill（下次 _ensure_proc 见 poll() 非空自动重启），而非永久阻塞。"""
        mod._proc = _spawn_silent_child()
        mod._proc_ready = True  # 跳过就绪探测，直击读路径
        wall = 2.0
        with patch.object(mod, "_REQUEST_TIMEOUT", wall):
            done, value = _run_with_wall(
                lambda: mod._call_hook("兰台超时探针", session_id="s73"), wall + 3.0
            )
        assert done, f"读路径无超时：_call_hook 超过墙宽 {wall}s+3s 余量仍阻塞在 read(1)"
        assert value is None, f"超时后应走空帧降级返回 None，实际 {value!r}"
        assert mod._proc is None or mod._proc.poll() is not None, (
            "超时子进程未被 kill，_ensure_proc 无法自动重启"
        )

    def test_call_dialogue_silent_child_returns_within_wall(self, mod):
        """对话写入（承诺 30s，含 LLM 提取）：沉默子进程下同样必须在墙内返回。"""
        mod._proc = _spawn_silent_child()
        mod._proc_ready = True
        wall = 2.0
        with patch.object(mod, "_DIALOGUE_TIMEOUT", wall):
            done, _ = _run_with_wall(
                lambda: mod._call_dialogue("对话片段", session_id="s73", turn=1), wall + 3.0
            )
        assert done, f"对话通道读路径无超时：_call_dialogue 超过墙宽 {wall}s+3s 余量仍阻塞"

    def test_wait_ready_silent_child_bounded(self, mod):
        """就绪探测：子进程活着但不吐行时，探测必须在 deadline 墙内返回 False。
        （旧实现 15s 截止只在两次阻塞读之间检查，第一次 read(1) 即永久阻塞。）"""
        mod._proc = _spawn_silent_child()
        mod._proc_ready = False
        done, value = _run_with_wall(lambda: mod._wait_ready(timeout=2.0), 8.0)
        assert done, "_wait_ready 超过 2s deadline + 6s 余量仍阻塞在探测读"
        assert value is False

    def test_after_timeout_next_call_restarts_and_recovers(self, mod, tmp_path):
        """票 73 关闭条件 2：超时 kill 后，下一次调用自动重启子进程恢复服务。

        重启用迷你 serve（真子进程真管道）验证全链路：_ensure_proc 见 poll()
        非空 → 重新 spawn → _wait_ready 探测通过 → 请求拿到合法帧。
        """
        mod._proc = _spawn_silent_child()
        mod._proc_ready = True
        with patch.object(mod, "_REQUEST_TIMEOUT", 2.0):
            done, value = _run_with_wall(lambda: mod._call_hook("第一枪", session_id=""), 6.0)
        assert done and value is None, "前置：沉默子进程应在墙内超时降级"
        proc = mod._proc
        assert proc is None or proc.poll() is not None, "超时子进程未被 kill"

        fake = tmp_path / "mini_serve_t73.py"
        fake.write_text(_MINI_SERVE, encoding="utf-8")
        with (
            patch.object(mod, "_VENV_PY", sys.executable),
            patch.object(mod, "_SHELL_HOOK", str(fake)),
            patch.object(mod, "_REQUEST_TIMEOUT", 5.0),
        ):
            done, data = _run_with_wall(
                lambda: mod._call_hook("恢复探针", session_id="s73b"), 15.0
            )
        assert done, "重启后的请求仍阻塞——子进程未按预期恢复服务"
        assert isinstance(data, dict) and data.get("event_id") == "ev_t73", (
            f"重启恢复后应拿到迷你 serve 的合法帧，实际 {data!r}"
        )


class TestNormalPathZeroDrift:
    """票 73 关闭条件 3：正常应答路径行为零漂移（真子进程即时应答）。"""

    def test_call_hook_normal_answer_unaffected(self, mod, tmp_path):
        """即时应答的 serve：注入帧完整返回，超时墙不误伤正常路径。"""
        fake = tmp_path / "mini_serve_t73.py"
        fake.write_text(_MINI_SERVE, encoding="utf-8")
        with (
            patch.object(mod, "_VENV_PY", sys.executable),
            patch.object(mod, "_SHELL_HOOK", str(fake)),
        ):
            done, data = _run_with_wall(
                lambda: mod._call_hook("兰台正常查询", session_id="s_ok"), 15.0
            )
        assert done, "正常应答路径竟阻塞——回归"
        assert isinstance(data, dict) and data.get("context") == "[mini-serve]", (
            f"正常路径应原样返回 serve 帧，实际 {data!r}"
        )
        # 应答完成后子进程必须还活着（watchdog 不得误杀健康子进程）
        assert mod._proc is not None and mod._proc.poll() is None, (
            "正常应答后子进程被误杀——超时墙伤及健康路径"
        )

    def test_call_dialogue_normal_answer_unaffected(self, mod, tmp_path):
        """对话写入正常路径：写入成功、无异常、子进程存活。"""
        fake = tmp_path / "mini_serve_t73.py"
        fake.write_text(_MINI_SERVE, encoding="utf-8")
        with (
            patch.object(mod, "_VENV_PY", sys.executable),
            patch.object(mod, "_SHELL_HOOK", str(fake)),
        ):
            done, _ = _run_with_wall(
                lambda: mod._call_dialogue("对话片段", session_id="s_ok", turn=1), 15.0
            )
        assert done, "对话写入正常路径竟阻塞——回归"
        assert mod._proc is not None and mod._proc.poll() is None, (
            "正常写入后子进程被误杀——超时墙伤及健康路径"
        )
