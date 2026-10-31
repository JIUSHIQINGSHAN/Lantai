"""协议面净化回归（v0.64 票 01 / N-01）。

病灶：`lantai/core/logger.py` 把 `lantai` logger 的 StreamHandler 绑到
sys.stdout；shell_hook --serve 的进程内任何 `logger.warning/info/...`
（如 `lantai.gate` 冒泡上来的 REMEMBRANCE_ENTITY_KEYWORDS 警告）都会
写进 stdout，污染 NDJSON 协议帧流——宿主侧逐行 `json.loads` 炸。

测试纪律：真实子进程跑 hook（不 mock 进程边界，与 test_host_matrix
同构），只对 embedding 外部网络用既有 sitecustomize 替身。断言的是
协议面本身：**stdout 每一行都必须是合法 JSON 帧**，日志只允许出现在
stderr。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "shell_hook.py"
STUB_DIR = REPO / "tests" / "support" / "host_matrix_stub"
PYTHON = sys.executable


def _child_env(home: Path) -> dict:
    env = dict(os.environ)
    env["LANTAI_HOME"] = str(home)
    env["LANTAI_HOST"] = "hermes"
    # 替身目录在前，使 sitecustomize 先于 lantai 生效（只替外部 embedding 网络）
    env["PYTHONPATH"] = os.pathsep.join([str(STUB_DIR), str(REPO)])
    env["LANTAI_TEST_STUB_EMBED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_serve_stdout_is_pure_ndjson(tmp_path):
    """stdout 是协议面：任何日志行（含第三方）不得混入，只允许合法 JSON 帧。"""
    home = tmp_path / "lantai_home"
    home.mkdir()
    init = subprocess.run(
        [PYTHON, "-c", "from lantai.storage.db import init_db; init_db()"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home),
        cwd=str(REPO),
        timeout=180,
    )
    assert init.returncode == 0, f"init_db 失败：{init.stderr[-800:]}"

    proc = subprocess.run(
        [PYTHON, str(HOOK), "--serve"],
        input='{"query": "部署怎么做"}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home),
        cwd=str(REPO),
        timeout=120,
    )
    assert proc.returncode == 0, f"hook 非零退出：{proc.stderr[-800:]}"

    # 协议面断言：逐行解析，任何非 JSON 行都是污染
    bad_lines = []
    frames = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines.append(line)
    assert not bad_lines, (
        "stdout 协议流被日志污染（NDJSON 帧混入非 JSON 行，宿主 json.loads 会炸）：\n"
        + "\n".join(f"  {ln[:120]}" for ln in bad_lines)
    )
    assert frames, "stdout 应至少有一个响应帧"
