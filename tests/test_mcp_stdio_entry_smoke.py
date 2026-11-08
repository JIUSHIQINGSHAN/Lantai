"""lantai.cli.mcp.main stdio 入口真冒烟（v0.68 诚实性审计 §5 登记的最后一项缺口）。

审计口径：main() 是 pyproject 控制台脚本 `lantai-mcp` 与 launcher_mcp.py 的
唯一入口契约，但 tests/ 此前只驱动过 handle()（帧级，test_mcp.py /
test_mcp_frame_gaps_smoke.py），main() 自身的行循环、静默契约（空行/坏帧/
超长行）、通知无响应、UTF-8 stdio 从未真实执行过（never-executed → 本文件收口）。

两个互补形态，零内部逻辑 mock：

1. test_stdio_loop_subprocess_end_to_end：子进程真拉起 main()——真 stdio 管道、
   真 UTF-8 配置、真进程退出码，黑盒验证行循环健壮性契约与中文回路
   （_configure_stdio_utf8 的存在理由：Windows 管道默认 GBK，中文乱码即红）。
2. test_main_loop_drives_core_tool_against_real_db：进程内 exec 副本加载
   （test_mcp.py 同款，防 handle() 的 LoopGuard 单例弄脏 _guard_instance 护栏
   通道），sys.stdin/stdout 以真实帧内容替换——属 I/O 接缝（同
   test_shell_hook.py 既有口径，纪律允许的流副作用替身）：行循环、JSON 解析、
   分发、handler、真库读全部真实执行。
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = ROOT / "lantai" / "cli" / "mcp.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server_stdio_entry", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stdio_loop_subprocess_end_to_end(tmp_path):
    """子进程真 stdio：合法帧各得一条响应；空行/坏帧/超长行/通知静默；中文完整回路。"""
    from lantai.cli.mcp import HERMES_CORE_TOOLS  # 只读导入：不调 handle，不实例化守卫

    env = dict(os.environ)
    home = tmp_path / "home"
    home.mkdir()
    env["LANTAI_HOME"] = str(home)
    env["REMEMBRANCE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)  # 钉住本树，防 ambient PYTHONPATH 污染（审计 §23 形态）
    env.pop("LANTAI_MCP_PROFILE", None)
    env.pop("MCP_TOOL_PROFILE", None)
    # 敌意编码固定（test_mcp_import_side_effects.py 同款取证手法）：强制子进程
    # 初始 stdio 为 GBK，让 _configure_stdio_utf8 的改写真正可观测——任何主机上
    # 中文回路失败都立即红，不靠本地 GBK 缺省碰巧绿。
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONUTF8"] = "0"

    frames = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"冒烟宿主"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',  # 通知：无响应
        '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}',  # 票 76：标准通知同样不得回帧
        "",  # 空行：静默
        "   ",  # 纯空白：静默
        "{not-json",  # 坏帧：静默
        '{"jsonrpc":"2.0","id":2,"method":"ping"}',
        '{"jsonrpc":"2.0","id":3,"method":"tools/list"}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"no_such_tool","arguments":{}}}',
        '{"jsonrpc":"2.0","id":5,"method":"不存在的方法"}',  # 中文方法名：读侧 UTF-8 回路
        '{"jsonrpc":"2.0","id":6,"method":"ping","params":{"pad":"'
        + "x" * 1_000_001
        + '"}}',  # 超长行：拒绝要有响应（票 76）——-32600 错误帧，不再静默吞
        '{"jsonrpc":"2.0","id":7,"method":"ping"}',  # 票 76：超长行拒绝后服务存活
    ]
    stdin_bytes = ("\n".join(frames) + "\n").encode("utf-8")

    proc = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", "from lantai.cli.mcp import main; main()"],
        input=stdin_bytes,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        timeout=300,  # 挂死防线（test_mcp_import_side_effects.py 同款），非时序断言
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]

    out_lines = proc.stdout.decode("utf-8").splitlines()
    # 12 帧进、恰 7 响应出：id 1-7；两条通知、空行×2、坏帧静默（票 76：
    # 超长带 id 不再静默，得 -32600 拒绝帧）；stderr 恰一条超长行留痕
    assert len(out_lines) == 7, f"响应行数应为 7（静默契约被破坏）: {out_lines[:3]}..."
    by_id = {}
    for raw in out_lines:
        resp = json.loads(raw)  # 每行都是独立合法 JSON（一帧一行的写侧契约）
        by_id[resp["id"]] = resp

    assert set(by_id) == {1, 2, 3, 4, 5, 6, 7}, sorted(by_id)
    assert by_id[1]["result"]["serverInfo"]["name"] == "lantai"
    assert by_id[1]["result"]["protocolVersion"]
    assert by_id[2]["result"] == {}
    advertised = {t["name"] for t in by_id[3]["result"]["tools"]}
    assert advertised == set(HERMES_CORE_TOOLS), (
        "默认档（未设 LANTAI_MCP_PROFILE）经 stdio 入口广告的必须恰为核心集"
    )
    assert by_id[4]["error"]["code"] == -32602
    # 写侧回路：响应里的中文原样出管（ensure_ascii=False + UTF-8），GBK 管道即乱码
    assert by_id[5]["error"]["message"] == "method not found: 不存在的方法"
    # 票 76：超长带 id 请求得 -32600 拒绝帧而非沉默，服务随即存活
    assert by_id[6]["error"]["code"] == -32600, by_id[6]
    assert by_id[7] == {"jsonrpc": "2.0", "id": 7, "result": {}}
    stderr_text = proc.stderr.decode("utf-8", "replace")
    assert "oversize" in stderr_text, stderr_text[-500:]


def test_oversize_line_with_id_rejected_with_frame_and_stays_alive(monkeypatch):
    """票 76：超长行拒绝要有响应——带 id 请求回 -32600 错误帧，不再静默吞。

    静默吞的旧契约下，带 id 的合法请求被丢后客户端拿不到任何响应只能等到
    超时（无错误帧、无留痕）。新契约：错误帧 + stderr 留痕一行（诊断通道，
    非协议面）+ 服务存活（下一帧 ping 正常应答）。
    """
    mod = _load_mcp()
    oversize = '{"jsonrpc":"2.0","id":9,"method":"ping","params":{"pad":"' + "x" * 1_000_001 + '"}}'
    frames = [oversize, '{"jsonrpc":"2.0","id":10,"method":"ping"}']
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(frames) + "\n"))
    fake_out, fake_err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", fake_out)
    monkeypatch.setattr("sys.stderr", fake_err)

    mod.main()

    out_lines = fake_out.getvalue().splitlines()
    assert len(out_lines) == 2, f"应恰两帧响应（拒绝帧 + 存活证明）: {fake_out.getvalue()[:200]}"
    reject, alive = (json.loads(raw) for raw in out_lines)
    assert reject["id"] == 9 and "error" in reject, reject
    assert reject["error"]["code"] == -32600
    assert alive == {"jsonrpc": "2.0", "id": 10, "result": {}}
    assert fake_err.getvalue().count("\n") == 1, fake_err.getvalue()
    assert "oversize" in fake_err.getvalue()


def test_oversize_line_without_id_stays_silent(monkeypatch):
    """票 76 验收 2 后半：无 id 的超长通知形态仍静默丢（协议面零响应）。"""
    mod = _load_mcp()
    oversize = (
        '{"jsonrpc":"2.0","method":"notifications/progress","params":{"pad":"'
        + "x" * 1_000_001
        + '"}}'
    )
    frames = [oversize, '{"jsonrpc":"2.0","id":11,"method":"ping"}']
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(frames) + "\n"))
    fake_out = io.StringIO()
    monkeypatch.setattr("sys.stdout", fake_out)
    monkeypatch.setattr("sys.stderr", io.StringIO())

    mod.main()

    out_lines = fake_out.getvalue().splitlines()
    assert len(out_lines) == 1, fake_out.getvalue()[:200]
    assert json.loads(out_lines[0]) == {"jsonrpc": "2.0", "id": 11, "result": {}}


def test_main_loop_drives_core_tool_against_real_db(param_env, monkeypatch):
    """真库块经 main() 全链读回：行循环→分发→handler→memory_service→DB 一路真实。"""
    from lantai.models.tables import CoreMemoryBlock

    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            CoreMemoryBlock(
                id="stdio-entry-smoke-01",
                block="identity",
                namespace="default",
                content="兰台 stdio 入口冒烟",
            )
        )
        s.commit()

    mod = _load_mcp()
    frame = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "core_memory_get", "arguments": {"namespace": "default"}},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(frame) + "\n"))
    fake_out = io.StringIO()
    monkeypatch.setattr("sys.stdout", fake_out)

    mod.main()  # stdin EOF 后自然退出

    resp = json.loads(fake_out.getvalue())  # 恰好一行响应
    assert resp["id"] == 7 and "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert [b["id"] for b in payload["blocks"]] == ["stdio-entry-smoke-01"], payload
    assert payload["blocks"][0]["content"] == "兰台 stdio 入口冒烟"
