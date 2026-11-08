"""插件墙必须严格长于服务端墙（巡检 2026-11-08 1200，票 73 后续）。

病灶：票 73 给插件对话通道加的 watchdog 墙写死 30s，而 serve 子进程自己的对话墙
``settings.SHELL_HOOK_DIALOGUE_TIMEOUT`` 默认也是 30s。两墙等宽时，慢提取那一轮到点
谁先触发是竞态——实测插件 watchdog 几乎总是先到，kill 整个 serve 子进程：服务端
本已放弃、正在后台跑完的写入线程被连带杀掉，这轮对话静默丢失，下一条还得冷启动。

纪律：集成用例不 mock 被测链路——真插件、真子进程真管道、真 shell_hook 的
``_dispatch_one`` + ``_run_with_timeout``（服务端墙经环境变量注入），只把对话写入
换成「睡 LAT 秒再写标记文件」的慢函数，等比缩放 30s 的真实墙宽。
"""

import contextlib
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).parent.parent
PLUGIN_PATH = REPO / "hermes-plugin" / "lantai-hook" / "__init__.py"

_WALL_ENV = ("SHELL_HOOK_TIMEOUT", "SHELL_HOOK_DIALOGUE_TIMEOUT")


def _load_plugin(name: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


@pytest.fixture()
def clean_env(monkeypatch):
    for k in _WALL_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


class TestWallDerivation:
    """墙宽推导：插件墙 = 服务端墙 + 宽限，口径与 serve 子进程一致。"""

    def test_default_dialogue_wall_strictly_exceeds_server_default(self, clean_env, tmp_path):
        from lantai.core.settings import Settings

        # 把 shell_hook 路径指到一个没有 .env 的临时仓库，隔离本机仓库根 .env 的覆盖
        (tmp_path / "scripts").mkdir()
        fake_hook = tmp_path / "scripts" / "shell_hook.py"
        fake_hook.write_text("", encoding="utf-8")
        clean_env.setenv("LANTAI_SHELL_HOOK", str(fake_hook))
        mod = _load_plugin("lantai_plugin_wall_default")
        # 直接看模块常量：插件真正用来挂 watchdog 的就是它们
        req, dia = mod._REQUEST_TIMEOUT, mod._DIALOGUE_TIMEOUT
        server_dialogue = Settings.model_fields["SHELL_HOOK_DIALOGUE_TIMEOUT"].default
        server_hook = Settings.model_fields["SHELL_HOOK_TIMEOUT"].default
        assert dia > server_dialogue, (
            f"对话通道插件墙 {dia}s 必须严格长于服务端默认墙 {server_dialogue}s，"
            "否则到点时 watchdog 与服务端降级竞态，kill 掉正在后台写入的子进程"
        )
        assert req > server_hook
        assert req == 5.0, "注入请求墙默认仍是票 73 承诺的 5s"
        # 默认常量与 settings 默认值必须同步（插件进程不 import lantai，只能镜像）
        assert server_dialogue == mod._SERVER_DIALOGUE_TIMEOUT_DEFAULT
        assert server_hook == mod._SERVER_HOOK_TIMEOUT_DEFAULT

    def test_module_constants_use_derived_walls(self, clean_env):
        mod = _load_plugin("lantai_plugin_wall_consts")
        server = mod._server_wall(
            "SHELL_HOOK_DIALOGUE_TIMEOUT", mod._SERVER_DIALOGUE_TIMEOUT_DEFAULT
        )
        assert server < mod._DIALOGUE_TIMEOUT, "模块常量必须是推导值，不是写死的 30s"
        assert mod._plugin_walls()[1] == mod._DIALOGUE_TIMEOUT
        assert mod._plugin_walls()[0] == mod._REQUEST_TIMEOUT

    def test_request_wall_default_keeps_5s_promise(self, clean_env, tmp_path):
        mod = _load_plugin("lantai_plugin_wall_req")
        req, _ = mod._plugin_walls(hook_path=str(tmp_path / "scripts" / "shell_hook.py"))
        assert req == 5.0, "服务端默认 2s 时注入请求墙仍是票 73 承诺的 5s，零漂移"

    def test_env_override_follows_server(self, clean_env, tmp_path):
        clean_env.setenv("SHELL_HOOK_DIALOGUE_TIMEOUT", "60")
        clean_env.setenv("SHELL_HOOK_TIMEOUT", "5")
        mod = _load_plugin("lantai_plugin_wall_env")
        req, dia = mod._plugin_walls(hook_path=str(tmp_path / "scripts" / "shell_hook.py"))
        assert dia == 65.0, "服务端墙调到 60s 时插件墙必须跟着加宽，否则 30s 就把子进程杀了"
        assert req == 8.0, "服务端注入墙调到 5s 时插件 5s 墙会与之等宽竞态，必须加宽"

    def test_dotenv_fallback_when_env_absent(self, clean_env, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / ".env").write_text(
            "# comment\n"
            "API_KEY=x\n"
            "export SHELL_HOOK_DIALOGUE_TIMEOUT=45  # 慢模型\n"
            'SHELL_HOOK_TIMEOUT="4"\n',
            encoding="utf-8",
        )
        mod = _load_plugin("lantai_plugin_wall_dotenv")
        req, dia = mod._plugin_walls(hook_path=str(tmp_path / "scripts" / "shell_hook.py"))
        assert dia == 50.0
        assert req == 7.0

    def test_env_beats_dotenv(self, clean_env, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / ".env").write_text("SHELL_HOOK_DIALOGUE_TIMEOUT=45\n", encoding="utf-8")
        clean_env.setenv("SHELL_HOOK_DIALOGUE_TIMEOUT", "20")
        mod = _load_plugin("lantai_plugin_wall_prio")
        _, dia = mod._plugin_walls(hook_path=str(tmp_path / "scripts" / "shell_hook.py"))
        assert dia == 25.0, "与 pydantic-settings 同口径：进程环境变量优先于 .env"

    @pytest.mark.parametrize("bad", ["abc", "", "0", "-3"])
    def test_invalid_value_falls_back_to_default(self, clean_env, tmp_path, bad):
        clean_env.setenv("SHELL_HOOK_DIALOGUE_TIMEOUT", bad)
        mod = _load_plugin("lantai_plugin_wall_bad")
        _, dia = mod._plugin_walls(hook_path=str(tmp_path / "scripts" / "shell_hook.py"))
        assert dia == 35.0


# 慢对话 serve：真 shell_hook 分发 + 真服务端墙，只把 _handle_dialogue 换成慢函数。
_SLOW_SERVE = """\
import os, sys, time
sys.path.insert(0, os.path.join(os.environ["REPRO_REPO"], "scripts"))
sys.path.insert(0, os.environ["REPRO_REPO"])
import shell_hook as sh

MARK = os.environ["REPRO_MARK"]
LAT = float(os.environ["REPRO_LAT"])

def _slow_dialogue(text, session_id="", turn=None):
    time.sleep(LAT)
    with open(MARK, "a", encoding="utf-8") as f:
        f.write(f"{session_id}|{turn}|{text}\\n")
    return {"ok": True}

sh._handle_dialogue = _slow_dialogue
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if '"query":""' in line.replace(" ", ""):
        sys.stdout.write("{}\\n"); sys.stdout.flush(); continue
    res = sh._dispatch_one(line)
    sys.stdout.write(sh.render_host_response(res) + "\\n")
    sys.stdout.flush()
"""


class TestSlowExtractionSurvivesServerWall:
    """集成：提取耗时超过服务端墙时，服务端降级先到、子进程存活、对话照常落库。"""

    SERVER_WALL = 1.0  # 等比缩放的 SHELL_HOOK_DIALOGUE_TIMEOUT（真实默认 30s）
    LATENCY = 2.5  # 慢提取（LLM 排队 + 调用）超过服务端墙

    @pytest.fixture()
    def slow_plugin(self, clean_env, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        mark = tmp_path / "dialogue_written.txt"
        serve = tmp_path / "slow_serve.py"
        serve.write_text(_SLOW_SERVE, encoding="utf-8")
        for k, v in {
            "REPRO_REPO": str(REPO),
            "REPRO_MARK": str(mark),
            "REPRO_LAT": str(self.LATENCY),
            "LANTAI_HOME": str(home),
            "API_KEY": "wall-margin-test",
            "SHELL_HOOK_DIALOGUE_TIMEOUT": str(self.SERVER_WALL),
        }.items():
            clean_env.setenv(k, v)
        mod = _load_plugin("lantai_plugin_wall_slow")
        mod._VENV_PY = sys.executable
        mod._SHELL_HOOK = str(serve)
        yield mod, mark
        proc = mod._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    def _wait_mark(self, mark: Path, deadline: float) -> list[str]:
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            if mark.exists() and mark.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        return mark.read_text(encoding="utf-8").strip().splitlines() if mark.exists() else []

    def test_slow_dialogue_lands_and_child_survives(self, slow_plugin):
        mod, mark = slow_plugin
        assert mod._DIALOGUE_TIMEOUT > self.SERVER_WALL, "插件墙未跟随服务端墙推导"
        proc = mod._ensure_proc()
        assert proc is not None and mod._wait_ready(timeout=60), "serve 未就绪"
        pid0 = proc.pid

        t0 = time.monotonic()
        mod._call_dialogue("我下周三要去协和医院复查甲状腺结节", session_id="s_wall", turn=1)
        elapsed = time.monotonic() - t0
        assert elapsed < self.LATENCY, "服务端降级帧应在服务端墙附近返回，不应等慢提取跑完"
        assert mod._proc is not None and mod._proc.poll() is None, (
            "serve 子进程被插件 watchdog 杀掉——插件墙未长于服务端墙，后台写入被连带丢弃"
        )

        written = self._wait_mark(mark, self.LATENCY + 5.0)
        assert written == ["s_wall|1|我下周三要去协和医院复查甲状腺结节"], (
            f"服务端放弃后后台线程应跑完落库，实际 {written!r}"
        )
        assert mod._ensure_proc().pid == pid0, "下一次调用不应触发冷启动"

    def test_equal_walls_reproduce_the_loss(self, slow_plugin):
        """对照组：把插件墙压回与服务端等宽（修复前形态），子进程被 kill、对话丢失。

        锁住「病灶确实来自等宽墙」，防止上一条绿在别的原因上。等宽竞态下 watchdog
        与服务端帧谁先到不完全确定，故插件墙取略短于服务端墙，确定性复现。
        """
        mod, mark = slow_plugin
        proc = mod._ensure_proc()
        assert proc is not None and mod._wait_ready(timeout=60)
        with patch.object(mod, "_DIALOGUE_TIMEOUT", self.SERVER_WALL - 0.2):
            mod._call_dialogue("对照组", session_id="s_eq", turn=1)
        # watchdog 只发 kill 信号不等回收，慢机器上立刻 poll 可能还是 None；等它真正退出
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        assert proc.poll() is not None, "对照组：插件墙短于服务端墙时子进程应被 kill"
        time.sleep(self.LATENCY + 1.0)
        assert not (mark.exists() and mark.read_text(encoding="utf-8").strip()), (
            "对照组：子进程被 kill 后对话不应落库"
        )


def test_env_var_names_match_settings():
    """插件读的两个环境变量名必须是 settings 的真实字段名（防改名后静默脱钩）。"""
    from lantai.core.settings import Settings

    for name in _WALL_ENV:
        assert name in Settings.model_fields, name
    assert os.path.isfile(PLUGIN_PATH)
