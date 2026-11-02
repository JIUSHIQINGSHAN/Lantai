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
    # 票 05：密钥前置必须由用例自己声明（shell_hook 的 assert_hook_api_key_configured
    # 要求非空 API_KEY 且禁止 DEV MODE 回退）。固定为与 CI 同口径的假密钥：
    # 既不再随宿主环境翻转红绿，也不会把真实密钥带进子进程。
    env["API_KEY"] = "test-key"
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


def test_child_env_declares_api_key_not_inherited(tmp_path, monkeypatch):
    """回归锁（票 05）：hook 密钥闸门要求非空 API_KEY（scripts/shell_hook.py:618，
    禁止 DEV MODE 回退），用例必须自带测试密钥，不得继承宿主环境——否则同一提交在
    配了/没配 API_KEY 的机器上给出不同红绿（实测 8 条随环境翻转），发布门禁不可复现。
    CI 的 env: API_KEY: test-key（.github/workflows/tests.yml）只是这条前置条件的
    外部补丁；补进测试后不再依赖宿主环境。
    """
    monkeypatch.delenv("API_KEY", raising=False)
    env = _child_env(tmp_path)
    assert env.get("API_KEY"), "_child_env 未声明 API_KEY：结果将随宿主环境漂移"
