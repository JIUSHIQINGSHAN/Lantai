"""票 71 红测：PUT /core-memory 契约改造——核心记忆块正文撤出 URL 查询串。

病灶（工单锚点，HEAD=6372a391 实测行号）：
1. studio.js:279-280 saveCoreBlock 把 identity/task/policy 三块正文经
   URLSearchParams 拼进 PUT /core-memory?block=...&content=... 查询串；
2. routes_memory.py:54-57 put_core_memory_route(block, content, ...) 设计为
   查询参数接收——契约本身把高敏正文放进 URL；
3. 性质：core block 是用户会放身份约束/政策口径/密钥口令的地方，URL 查询串
   与 body 的本质差别是 uvicorn 访问日志默认含 query string（票 50 的
   loggable 管日志插值，管不到 URL 本身——必须改契约）。

普查（工单要求全仓 grep 复核，命令与结论进回执）：
- 活消费方仅两处：REST 路由 routes_memory.py:54 + studio.js:280（UI）；
- MCP 层无 put 工具（core_memory_get 只读面 :1371-1379/:3092/:3952/:4014），
  票面「MCP put_core_memory 是调用方之一」前提在活代码中不成立
  → 按兼容窗规则拍板：直接切换契约、不留兼容层；
- .scratch/、build/ 下为基线快照/构建残影，非活消费方，排除；
- 测试面 query-form 调用点 4 处（test_console_api_contracts.py:125、
  test_e2e.py:138/143/296）随票更新为新契约；
- README.md:120 端点表 method/path 不变无需改；docs/WHITEPAPER.md:245 同。

红臂取证机制（派单 TDD 要求 1 的技术警告：TestClient 走 ASGI transport
不触发 uvicorn access logger，tests/ 无捕获先例）：
真 uvicorn.Server 后台线程（lifespan="off" 绕开 apscheduler job 序列化
炸点——预研实测，取证面只需 HTTP 进路由不需应用级 lifespan）+ 真套接字
临时端口 + urllib 真请求 + caplog 捕获 uvicorn.access logger——全链路
真实机制取证，非 mock 非 formatter 改写，被测链路（HTTP→路由→库）全真。

纪律：真库直调禁 mock 被测链路；测试库走 conftest 隔离（红线：零连接触碰
LANTAI_HOME 真实开发库）。
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

SECRET = "SECRET-policy-token-ticket71"

VALID_BLOCKS = ("identity", "task", "policy")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def live_server(caplog):
    """真 uvicorn.Server 后台线程（隔离库 + 真套接字端口），取证 uvicorn.access。"""
    from lantai.api.app import app

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="WARNING",
        access_log=True,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    assert server.started, "真 uvicorn server 未能在 15s 内就绪"

    import logging

    caplog.set_level(logging.INFO, logger="uvicorn.access")
    yield port
    server.should_exit = True
    t.join(timeout=10)


def _access_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "uvicorn.access"]


def _http_put(port: int, target: str, *, json_body: bytes | None, token: str) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{target}",
        data=json_body,
        method="PUT",
        headers={"X-API-Key": token, "Content-Type": "application/json"}
        if json_body is not None
        else {"X-API-Key": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


# ── 臂 1：泄漏臂（现状红：SECRET 明文进 uvicorn.access）────────────────────


def test_arm1a_query_string_secret_hits_access_log(live_server, caplog):
    """旧 query 契约**拒收锁**（票 71 契约切换后）：PUT /core-memory?block=...&
    content=SECRET → 422（body 契约下 FastAPI query 参数校验拒绝，旧形态不可
    再携带正文入库），且 SECRET 不落库（GET 读回无该内容）。红阶段的现状锁
    实锤过 SECRET 进 uvicorn.access（红日志 ticket71-red.log），修复后此臂
    转为防旧形态回潮的拒收面。"""
    import json as _json

    port = live_server
    token = (
        "test"  # conftest DEV MODE：settings.API_KEY 已被清空，X-API-Key 失配走 default principal
    )
    status = _http_put(
        port,
        f"/core-memory?block=policy&content={urllib.request.quote(SECRET)}",
        json_body=None,
        token=token,
    )
    assert status == 422, f"旧 query 形态应被拒收 422（body 契约），实际 {status}"
    lines = _access_lines(caplog)
    assert lines, "uvicorn.access 无记录（取证机制失效）"

    # 旧形态未能写库（读回验证——正文没进去）
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/core-memory?namespace=default",
        headers={"X-API-Key": token},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode())
    blocks = data.get("blocks", []) if isinstance(data, dict) else []
    assert not any(SECRET in str(b) for b in blocks), (
        f"旧 query 形态的正文穿透进了库（拒收面失守）：{blocks}"
    )


def test_arm1b_body_secret_never_in_access_log(live_server, caplog):
    """修复后契约（body 形态）：PUT /core-memory JSON body → uvicorn.access
    不含 SECRET、请求 200、真库读回成功。"""
    import json as _json

    port = live_server
    body = _json.dumps(
        {"block": "policy", "content": f"政策口径 {SECRET}", "namespace": "default"}
    ).encode()
    status = _http_put(port, "/core-memory", json_body=body, token="test")
    assert status == 200, f"body 形态应 200，实际 {status}"

    lines = _access_lines(caplog)
    assert lines, "uvicorn.access 无记录（取证机制失效）"
    joined = "\n".join(lines)
    assert SECRET not in joined, f"SECRET 泄漏进访问日志（URL 查询串病灶仍在）：\n{lines[:3]}"

    # 真库读回（GET 走同 server 的真 HTTP 面）
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/core-memory?namespace=default",
        headers={"X-API-Key": "test"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode())
    blocks = data.get("blocks", []) if isinstance(data, dict) else []
    assert any(SECRET in str(b) for b in blocks), f"body 写入未落库（读回失败）：{data}"


# ── 臂 2：功能回归臂（TestClient 真内存库双面）────────────────────────────


@pytest.fixture()
def client(monkeypatch):
    """同 test_console_api_contracts.py 的 client 夹具（文件内夹具非全局）。"""
    from fastapi.testclient import TestClient

    import lantai.core.auth as auth_module
    from lantai.api.app import app

    monkeypatch.setattr(auth_module, "db_has_api_key", lambda: False)
    with TestClient(app) as c:
        yield c


def test_arm2_put_body_roundtrip_and_invalid_block(client):
    """修复后契约功能面：body PUT → GET 读回新内容；非法 block 400（旧契约
    400 语义保持，test_e2e.py:143 同形）。"""
    res = client.put(
        "/core-memory",
        json={"block": "task", "content": "票 71 回归任务基线", "namespace": "default"},
    )
    assert res.status_code == 200, res.text
    got = client.get("/core-memory")
    assert got.status_code == 200
    assert "票 71 回归任务基线" in got.text, "body 写入读回失败"

    bad = client.put("/core-memory", json={"block": "invalid", "content": "x"})
    assert bad.status_code == 400, f"非法 block 应 400（契约语义保持），实际 {bad.status_code}"


# ── 臂 3：普查臂（query-form 调用零残留 + MCP 只读面不受影响）──────────────


def test_arm3_no_query_form_callers_remain():
    """普查臂：活代码面（lantai/ + tests/ + docs/ + README）无残留 **PUT
    query-form** core-memory 调用（GET ?namespace= 是合法形态不在病灶面）；
    MCP core_memory_get 只读面照旧。.scratch/、build/ 为快照/残影排除
    （工单口径）。本文件自证行（docstring/红臂取证串）豁免。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    scan_roots = [root / "lantai", root / "tests", root / "docs", root / "README.md"]
    # 病灶形状：PUT + core-memory? 同行（GET 读面带 query 参数是合法契约；
    # studio.js 模板串形态 `core-memory?${params}` 由第三分支抓取；
    # TestClient params= 分行形态由第四分支抓取——core-memory 行之后 3 行内
    # 出现 params= + block/content 键即 query-form PUT）
    pattern = re.compile(
        r"(put|PUT)[^\n]*core-memory\?"
        r"|core-memory\?[^\n]*(block|content)"
        r"|core-memory\?\$\{"
        r'|params\s*=\s*\{[^}]*"(block|content)"'
    )
    self_file = str(Path(__file__).relative_to(root)).replace("\\", "/")
    for base in scan_roots:
        files = (
            [base]
            if base.is_file()
            else [p for p in base.rglob("*") if p.suffix in (".py", ".js", ".md")]
        )
        for f in files:
            rel = str(f.relative_to(root)).replace("\\", "/")
            if rel == self_file:
                continue  # 本文件自证行（docstring 引用病灶原文）豁免
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if pattern.search(ln):
                    offenders.append(f"{rel}:{i}: {ln.strip()[:80]}")
    assert not offenders, f"残留 query-form core-memory PUT 调用（普查臂）：\n{offenders}"

    # MCP 只读面不受影响：注册与 handler 照旧（票面普查前提复核）
    from lantai.cli.mcp import TOOL_HANDLERS, TOOLS

    assert "core_memory_get" in TOOLS and "core_memory_get" in TOOL_HANDLERS
    assert "put_core_memory" not in TOOLS, "MCP put 工具不应存在（普查前提）"
