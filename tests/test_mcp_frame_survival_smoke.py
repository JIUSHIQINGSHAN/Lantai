"""票 52：MCP `checkpoint_latest` 有底本数据后 100% 炸 + 单帧异常杀死整个 stdio 服务。

`.scratch/v0.68-next-window-2026-11/issues/52-mcp-checkpoint-latest-crashes-stdio.md`

**先说影响**（三层同病，一层比一层重）：

1. `_rows_to_checkpoint` 把 ORM 裸 `datetime` 直接放进返回 dict——MCP 面没有
   FastAPI `jsonable_encoder` 兜底，`json.dumps` 抛 `TypeError`（REST 面同数据不炸，
   与 digest 票「只有 MCP 侧炸」完全同构）。
2. 炸点在守卫外：`handle()` 的 try 只包 `handler(args)`，响应组装（dumps）在其后
   裸跑——TypeError 冲出 `handle()`。
3. `main()` 行循环对 `handle(msg)` 与响应帧 dumps 同样裸调——一帧的序列化病杀死
   整个 stdio 服务进程，**全部工具失联**（宿主看到的不是 -32603 而是服务器死亡）。
   巡检对账补强：更宽的触发器是非对象 JSON 帧（`[1]`）在 `msg.get("id")` 即
   AttributeError——无需任何工具、无需数据、默认档/全量档都中
   （`echo '[1]' | python -m lantai.cli.mcp` 单行复现；shell_hook 侧
   `host_protocol.parse_host_request` 已修过一模一样的病，MCP 侧漏修）。

**修法**：`_rows_to_checkpoint.created_at` 过 `isoformat()`（全仓出口约定）；
响应组装挪进守卫（dumps 抛错回 `-32603 response serialization failed` 且计入
loop guard 失败——非序列化错混不进 -32602/参数错）；`main()` 循环兜底 try +
`_emit_error_frame`（单帧只值一条错误响应，服务必须活下去）；非对象帧回
`-32600`；登记项随票：MCP 出口与 `host_protocol.render_host_response` 统一
`allow_nan=False`（NaN 不可达有审计背书，纯把静默非法 JSON 转成显式错），
shell_hook serve 循环 render 加守卫（开 allow_nan=False 后不得引入新崩溃向量）。

**测试边界声明**：进程内臂走 exec 副本加载（防真 import 的 LoopGuard 单例
弄脏 `_guard_instance`，test_mcp_stdio_entry_smoke 同款）+ `param_env` 真内存库；
stdin/stdout 以真实帧内容替换属 I/O 接缝替身（既有口径）；子进程臂真 stdio
管道真退出码（LANTAI_HOME 钉进 tmp，full 档显式声明）；序列化守卫臂以
`TOOL_HANDLERS` 注册表注入测试 handler（被测的是 dispatch→守卫→组装链，
非 profile 选择逻辑）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.mcp_full_surface()

ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = ROOT / "lantai" / "cli" / "mcp.py"

BLOCKS = {
    "cp_active_intent": "正在收口票 52 stdio 冻结面",
    "cp_next_action": "全量测试确认无回归后推送",
}


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server_frame_survival", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(mid, method: str, params: dict | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, ensure_ascii=False)


class TestCheckpointLatestChain:
    """TDD 1：底本写→读全链。修复前 id=3 帧杀死进程，ping 帧永远得不到响应。"""

    def test_write_then_latest_survives_and_iso(self, param_env, monkeypatch):
        mod = _load_mcp()
        frames = [
            _frame(1, "initialize"),
            _frame(
                2,
                "tools/call",
                {
                    "name": "checkpoint_write",
                    "arguments": {"session_id": "sess-ticket-52", "blocks": BLOCKS},
                },
            ),
            _frame(3, "tools/call", {"name": "checkpoint_latest", "arguments": {}}),
            _frame(4, "ping"),
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(frames) + "\n"))
        fake_out = io.StringIO()
        monkeypatch.setattr("sys.stdout", fake_out)

        mod.main()  # 修复前：id=3 帧的 TypeError 冲出 main()，测试直接红

        lines = [json.loads(x) for x in fake_out.getvalue().splitlines()]
        by_id = {r["id"]: r for r in lines}
        assert set(by_id) == {1, 2, 3, 4}, (
            f"服务必须活过 id=3 帧（响应应含 1/2/3/4）：{sorted(by_id)}"
        )
        assert "error" not in by_id[2], f"checkpoint_write 应成功：{by_id[2]}"
        payload = json.loads(by_id[3]["result"]["content"][0]["text"])
        created = payload["created_at"]
        assert isinstance(created, str), (
            f"created_at 应为 ISO 字符串，实为 {type(created).__name__}"
        )
        datetime.fromisoformat(created)  # 全仓出口约定：ISO 可解析
        assert by_id[4].get("result") == {}, f"id=4 ping 必须照常响应（服务存活断言）：{by_id[4]}"

    def test_subprocess_full_profile_chain(self, tmp_path):
        """子进程真拉起（full 档 + 真 tmp 库）：黑盒验证进程存活与 ISO 出口。"""
        env = dict(os.environ)
        home = tmp_path / "home"
        home.mkdir()
        env["LANTAI_HOME"] = str(home)
        env["REMEMBRANCE_HOME"] = str(home)
        env["LANTAI_MCP_PROFILE"] = "full"
        env["PYTHONPATH"] = str(ROOT)  # 钉住本树，防 ambient PYTHONPATH 污染
        frames = [
            _frame(1, "initialize"),
            _frame(
                2,
                "tools/call",
                {
                    "name": "checkpoint_write",
                    "arguments": {"session_id": "sess-subproc-52", "blocks": BLOCKS},
                },
            ),
            _frame(3, "tools/call", {"name": "checkpoint_latest", "arguments": {}}),
            _frame(4, "ping"),
        ]
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from lantai.storage.db import init_db; init_db(); "
                "from lantai.cli.mcp import main; main()",
            ],
            input=("\n".join(frames) + "\n").encode("utf-8"),
            capture_output=True,
            cwd=str(ROOT),
            env=env,
            timeout=300,  # 挂死防线（test_mcp_stdio_entry_smoke 同款），非时序断言
        )
        assert proc.returncode == 0, (
            f"修复前 checkpoint_latest 一帧即杀死 stdio 进程："
            f"{proc.stderr.decode('utf-8', 'replace')[-800:]}"
        )
        by_id = {}
        for raw in proc.stdout.decode("utf-8").splitlines():
            resp = json.loads(raw)
            by_id[resp["id"]] = resp
        assert set(by_id) == {1, 2, 3, 4}, f"响应应含 1/2/3/4：{sorted(by_id)}"
        payload = json.loads(by_id[3]["result"]["content"][0]["text"])
        assert isinstance(payload["created_at"], str)
        datetime.fromisoformat(payload["created_at"])


class TestSerializationGuard:
    """TDD 2（结构臂）：守卫只包了 handler，dumps 在外裸奔——序列化病必须只值一条 -32603。"""

    def test_datetime_result_becomes_32603_and_loop_lives(self, monkeypatch):
        mod = _load_mcp()
        from lantai.core.time import utcnow

        monkeypatch.setitem(
            mod.TOOL_HANDLERS, "checkpoint_latest", lambda params: {"when": utcnow()}
        )
        r1 = mod.handle(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "checkpoint_latest", "arguments": {}},
            }
        )  # 修复前：TypeError 冲出 handle()，红
        assert "error" in r1, f"裸 datetime 应转错误帧而非外抛：{r1}"
        assert r1["error"]["code"] == -32603, r1
        assert "serialization" in r1["error"]["message"], r1
        r2 = mod.handle({"jsonrpc": "2.0", "id": 11, "method": "ping"})
        assert r2["id"] == 11 and r2.get("result") == {}, f"同进程下一帧必须照常响应：{r2}"

    def test_nan_result_becomes_32603_not_silent_nan(self, monkeypatch):
        """登记项臂：allow_nan=True 的静默非法 JSON 出口必须转显式错。"""
        mod = _load_mcp()
        monkeypatch.setitem(
            mod.TOOL_HANDLERS, "checkpoint_latest", lambda params: {"score": float("nan")}
        )
        r = mod.handle(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "checkpoint_latest", "arguments": {}},
            }
        )
        assert "error" in r, f"NaN 结果应转 -32603，不得静默产出非法 JSON 帧：{r}"
        assert r["error"]["code"] == -32603, r
        assert "NaN" not in json.dumps(r), "错误帧自身不得携带 NaN 字面量"


class TestNonObjectFrameSurvival:
    """巡检对账补强臂：`[1]` 帧在 `msg.get("id")` 即 AttributeError 杀死进程——
    无需工具、无需数据、任何档位都中。修复后回 -32600 且后续帧照常。"""

    def test_non_object_frame_does_not_kill_stdio(self, tmp_path):
        env = dict(os.environ)
        home = tmp_path / "home"
        home.mkdir()
        env["LANTAI_HOME"] = str(home)
        env["REMEMBRANCE_HOME"] = str(home)
        env.pop("LANTAI_MCP_PROFILE", None)
        env.pop("MCP_TOOL_PROFILE", None)
        env["PYTHONPATH"] = str(ROOT)
        stdin = "[1]\n" + _frame(2, "ping") + "\n"
        proc = subprocess.run(
            [sys.executable, "-c", "from lantai.cli.mcp import main; main()"],
            input=stdin.encode("utf-8"),
            capture_output=True,
            cwd=str(ROOT),
            env=env,
            timeout=300,
        )
        assert proc.returncode == 0, (
            f"修复前非对象帧杀死 stdio 进程：{proc.stderr.decode('utf-8', 'replace')[-800:]}"
        )
        out = [json.loads(x) for x in proc.stdout.decode("utf-8").splitlines()]
        by_id = {r.get("id"): r for r in out}
        assert by_id.get(None, {}).get("error", {}).get("code") == -32600, (
            f"[1] 帧应回 -32600：{out}"
        )
        assert by_id.get(2, {}).get("result") == {}, f"坏帧后的合法帧必须照常响应：{out}"


class TestRestFaceUnchanged:
    """TDD 3（对照臂）：REST 面同数据本就不炸（FastAPI 编码器兜底）——行为逐字不变。"""

    def test_rest_latest_iso_unchanged(self, param_env):
        from lantai.api.app import app

        client = TestClient(app)
        w = client.post(
            "/checkpoint",
            json={"session_id": "sess-rest-52", "blocks": BLOCKS},
            headers={"X-API-Key": "test"},
        )
        assert w.status_code == 200, w.text
        r = client.get("/checkpoint/latest", headers={"X-API-Key": "test"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == "sess-rest-52", body
        created = body["created_at"]
        assert isinstance(created, str), f"REST 面 created_at 修复前后都应为 ISO 字符串：{body}"
        datetime.fromisoformat(created)
