"""MCP stdio 桥的帧边界与坏帧存活（巡检 2026-11-24，模块四：客户端适配）。

病灶：`scripts/lantai_mcp_bridge.py` 的 `main()` 用 `for line in sys.stdin` 先把
**整行**读进内存再判 `len(line) > 1_000_000`——上限形同虚设，一个超大坏帧即可把
桥进程撑爆；宿主只看到 EOF，整条记忆通道失联。同仓库打包版
`lantai/cli/mcp_bridge.py` 已按上限 `readline`，此副本没有对齐。

本测试走真实子进程 + 真实 stdio（不 mock 进程边界）：超长帧必须回 -32600、
坏 UTF-8/坏 JSON 帧必须静默跳过，且**后续合法帧照常应答**（循环不死）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "lantai_mcp_bridge.py"
MAX_FRAME = 1_000_000


def _run(payload: bytes) -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=payload,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"桥进程非零退出：{proc.stderr[-800:]!r}"
    return [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]


def _ping() -> bytes:
    return b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'


def test_oversize_frame_rejected_and_loop_continues():
    """超长帧回 -32600（不撑爆内存），下一帧照常应答。"""
    oversize = b'{"x":"' + b"a" * (MAX_FRAME + 10) + b'"}\n'
    frames = _run(oversize + _ping())

    assert any(f.get("error", {}).get("code") == -32600 for f in frames), (
        f"超长帧应回 -32600，实得 {frames!r}"
    )
    assert any(f.get("id") == 1 and f.get("result") == {} for f in frames), (
        f"超长帧之后循环应继续应答 ping，实得 {frames!r}"
    )


def test_bad_utf8_frame_skipped_and_loop_continues():
    """坏 UTF-8 帧不得打死循环（解码失败静默跳过，无 id 无从应答）。"""
    frames = _run(b"\xff\xfe\x00 not utf8\n" + _ping())

    assert len(frames) == 1, f"坏编码帧不应产生应答，实得 {frames!r}"
    assert frames[0]["id"] == 1 and frames[0]["result"] == {}


def test_malformed_json_frame_skipped_and_loop_continues():
    """坏 JSON 帧静默跳过，后续合法帧照常应答。"""
    frames = _run(b"not-json{{{\n" + _ping())

    assert len(frames) == 1, f"坏 JSON 帧不应产生应答，实得 {frames!r}"
    assert frames[0]["id"] == 1 and frames[0]["result"] == {}
