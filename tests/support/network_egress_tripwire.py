"""网络出口绊线（票 05 续15）：把「测试进程有没有真的出网」变成可取证的事实。

与 `real_home_tripwire.py` 同一形态——**只监听，不阻断**：每次 `socket.socket.connect`
都原样放行，非环回的那一次记一行台账（用例 nodeid + 目标 host/port + 栈尾）。
判定的是出口事实本身，不是把网络请求改掉，因此不会替被测代码「修好」任何东西。

为什么需要它：`_stub_external_llm` 的替身网覆盖 18 个模块绑定，但「覆盖得全不全」过去
只能靠人读；`OPENAI_BASE_URL` 被装配策略强制成黑洞以后，逃逸的后果从「真外传」降级成
「连接失败」，可仍然**没有一个观测点**。绊线装上后端点是什么都无所谓：任何非环回
connect 都会被点名。

装载（只加监听，不改测试语义）：
    $env:PYTHONPATH = "tests/support"
    $env:LANTAI_GATE_EGRESS_REPORT = "<path>"
    pytest tests/ -p network_egress_tripwire
不给 `LANTAI_GATE_EGRESS_REPORT` 就整个不装载（零开销）；调用方须把「报告缺失」
按**未取证**处理，不能当成通过。装载时会先落一个 `armed: true` 的头，会话结束再写全量。

台账里唯一被允许出现的出网尝试是绊线自检用例自己（`DECLARED_EGRESS_PROBES` 按 nodeid
申报）。门禁判据取 `undeclared`（未申报计数），不是 `count`——否则「把违规说成自检」
和「把自检说成违规」都只凭一句话。白名单是源码里的固定 frozenset，被测代码改不动它；
自检用例改名而不更新白名单会直接判红。
生命周期：`pytest_configure` 打补丁，`pytest_unconfigure` 无条件还原 `socket.socket.connect`。
"""

import ipaddress
import json
import os
import socket
import traceback

MAX_RECORDS = 50

# 绊线自检必须真连一次不可路由保留地址（RFC 5737），否则 count=0 的绿灯什么都证明不了。
# 这条出网是申报过的；除此以外的任何非环回 connect 都进 undeclared。
DECLARED_EGRESS_PROBES = frozenset(
    {"tests/test_network_egress_tripwire.py::test_records_remote_and_spares_loopback"}
)

_current = {"nodeid": "<pre-collect>"}
_attempts: list[dict] = []
_state = {"report": "", "patched": False, "orig_connect": None}


def classify(address) -> tuple[str, object, object]:
    """把 connect 目标分成 loopback / remote / other（分类失败一律按 other 记账）。"""
    try:
        if isinstance(address, tuple):
            host = address[0]
            port = address[1] if len(address) > 1 else None
            if isinstance(host, bytes):
                host = host.decode("utf-8", "replace")
            try:
                ip = ipaddress.ip_address(str(host))
            except ValueError:
                if str(host).strip().lower() in ("", "localhost"):
                    return "loopback", host, port
                return "remote", host, port
            return ("loopback" if ip.is_loopback else "remote"), host, port
        return "other", str(address), None
    except Exception as exc:  # noqa: BLE001 - 分类本身不许影响被测行为
        return "other", f"<unclassified {type(exc).__name__}>", None


def undeclared_attempts(attempts: list[dict] | None = None) -> list[dict]:
    """未申报的出网尝试：只有它为空，才证明没人真的把数据送到机器外面。"""
    source = _attempts if attempts is None else attempts
    return [a for a in source if a.get("test") not in DECLARED_EGRESS_PROBES]


def _write(payload: dict) -> None:
    path = _state["report"]
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _snapshot(extra: dict | None = None) -> dict:
    data = {
        "armed": True,
        "count": len(_attempts),
        "undeclared": len(undeclared_attempts(_attempts)),
        "declared_probes": sorted(DECLARED_EGRESS_PROBES),
        "attempts": _attempts[:MAX_RECORDS],
        "truncated": len(_attempts) > MAX_RECORDS,
    }
    if extra:
        data.update(extra)
    return data


def pytest_configure(config):
    if _state["patched"]:  # 幂等：重复装载不许把 tracer 当成「原函数」记下来（那会永久留下补丁）
        return
    _state["report"] = os.environ.get("LANTAI_GATE_EGRESS_REPORT", "").strip()
    if not _state["report"]:
        return  # 未装载：调用方按「未取证」处理
    _attempts.clear()
    _state["orig_connect"] = socket.socket.connect

    def traced_connect(self, address, *args, **kwargs):
        kind, host, port = classify(address)
        if kind != "loopback" and len(_attempts) < MAX_RECORDS + 1:
            stack = "".join(traceback.format_stack()[:-1][-8:])
            _attempts.append(
                {
                    "test": _current["nodeid"],
                    "kind": kind,
                    "host": str(host),
                    "port": port,
                    "stack": stack,
                }
            )
        return _state["orig_connect"](self, address, *args, **kwargs)

    socket.socket.connect = traced_connect
    _state["patched"] = True
    _write(_snapshot())  # 先证明监控确实装上了


def pytest_runtest_setup(item):
    _current["nodeid"] = getattr(item, "nodeid", "<unknown>")


def pytest_sessionfinish(session, exitstatus=0):
    if not _state["report"]:
        return
    _write(_snapshot({"exit": int(exitstatus)}))


def pytest_unconfigure(config):
    """无条件还原：绊线自己也不许把全局留下（G2 同一口径）。"""
    if _state["patched"] and _state["orig_connect"] is not None:
        socket.socket.connect = _state["orig_connect"]
    _state["patched"] = False
    _state["orig_connect"] = None
