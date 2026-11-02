"""网络出口绊线自身的回归（票 05 续15）：可装载、可归因、可还原、不误报环回。

不 mock 被测逻辑：真调 `socket.connect`，真读绊线写出的台账文件。远程目标用
RFC 5737 TEST-NET-1 的 `192.0.2.1`（保留不可路由地址）+ 极短超时，**不会真的出网**，
又能证明「非环回」这一类确实被抓得到——没有这条对照，count=0 的绿灯说明不了任何事。
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support import network_egress_tripwire as net

LOOPBACK_REFUSED_PORT = 1  # 环回上几乎必然没人监听：立刻 ECONNREFUSED，不出网
REMOTE_UNROUTABLE = "192.0.2.1"  # TEST-NET-1，保留地址，不可路由


def _try_connect(host, port, timeout=0.3):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except OSError:
        return "oserror"
    finally:
        sock.close()
    return "connected"


@pytest.fixture()
def armed(tmp_path, monkeypatch):
    """装载绊线并在用例结束后无条件还原全局。"""
    report = tmp_path / "egress.json"
    monkeypatch.setenv("LANTAI_GATE_EGRESS_REPORT", str(report))
    original = socket.socket.connect
    net.pytest_configure(SimpleNamespace())
    try:
        yield report, original
    finally:
        net.pytest_sessionfinish(SimpleNamespace(), 0)
        net.pytest_unconfigure(SimpleNamespace())
        assert socket.socket.connect is original, "绊线没把 socket.socket.connect 还回来"


def test_not_armed_without_report_path(monkeypatch):
    """没给报告路径 = 整个不装载（零开销），调用方须按「未取证」处理。"""
    monkeypatch.delenv("LANTAI_GATE_EGRESS_REPORT", raising=False)
    original = socket.socket.connect
    net.pytest_configure(SimpleNamespace())
    assert socket.socket.connect is original, "未给报告路径却打了补丁"
    assert net._state["patched"] is False
    assert net._state["report"] == ""


def test_classify_targets():
    """分类判据：环回（含 IPv6/localhost）不算出网，公网与保留地址算，非 inet 走 other。"""
    assert net.classify(("127.0.0.1", 5672))[0] == "loopback"
    assert net.classify(("::1", 8767, 0, 0))[0] == "loopback"
    assert net.classify(("localhost", 80))[0] == "loopback"
    assert net.classify(("baidu.com", 443))[0] == "remote"
    assert net.classify(("8.8.8.8", 53))[0] == "remote"
    assert net.classify("/tmp/lantai.sock")[0] == "other"
    assert net.classify(None)[0] == "other"


def test_arming_writes_head_and_restores_global(armed):
    """装载即落 armed 头（防止「静默不检测」），退出即还原全局。"""
    report, original = armed
    assert socket.socket.connect is not original, "装载后 connect 未被监听"
    head = json.loads(report.read_text(encoding="utf-8"))
    assert head["armed"] is True


def test_records_remote_and_spares_loopback(armed, request):
    """真出网意图必须被点名；环回不许算作出网；自检出网必须被正确申报。"""
    report, _ = armed
    nodeid = request.node.nodeid
    assert nodeid in net.DECLARED_EGRESS_PROBES, f"自检用例改名/改路径后没更新申报白名单: {nodeid}"
    net.pytest_runtest_setup(SimpleNamespace(nodeid=nodeid))

    assert _try_connect("127.0.0.1", LOOPBACK_REFUSED_PORT) in ("oserror", "connected")
    assert _try_connect(REMOTE_UNROUTABLE, 9) in ("oserror", "connected")

    net.pytest_sessionfinish(SimpleNamespace(), 0)
    data = json.loads(report.read_text(encoding="utf-8"))
    hosts = [a["host"] for a in data["attempts"]]
    assert REMOTE_UNROUTABLE in hosts, f"保留地址的 connect 意图没被记下来：{data}"
    assert "127.0.0.1" not in hosts, f"环回被误记为出网：{data}"
    record = next(a for a in data["attempts"] if a["host"] == REMOTE_UNROUTABLE)
    assert record["kind"] == "remote"
    assert record["test"] == nodeid, "台账没有归因到具体用例"
    assert "test_network_egress_tripwire.py" in record["stack"]
    assert data["count"] == len(data["attempts"])
    assert data["undeclared"] == 0, f"自检出网被算成违规: {data}"
    assert data["declared_probes"] == sorted(net.DECLARED_EGRESS_PROBES)


def test_double_arm_does_not_stack_patches(monkeypatch, tmp_path):
    """幂等：重复装载不许把 tracer 记成「原函数」，否则 unconfigure 还原的是补丁。"""
    report = tmp_path / "egress.json"
    monkeypatch.setenv("LANTAI_GATE_EGRESS_REPORT", str(report))
    original = socket.socket.connect
    net.pytest_configure(SimpleNamespace())
    first = socket.socket.connect
    net.pytest_configure(SimpleNamespace())
    assert socket.socket.connect is first
    net.pytest_unconfigure(SimpleNamespace())
    assert socket.socket.connect is original, "第二次装载导致补丁残留"


def test_declared_sentinel_excludes_only_declared_records():
    """申报哨兵是纯谓词：白名单内的记录被排除，其余（含缺 test 字段）一律留下。"""
    declared = sorted(net.DECLARED_EGRESS_PROBES)[0]
    rows = [
        {"test": declared, "host": "192.0.2.1"},
        {"test": "tests/test_other.py::test_anything", "host": "8.8.8.8"},
        {"test": declared, "host": "192.0.2.2"},
        {"host": "no-test-field"},
    ]
    kept = net.undeclared_attempts(rows)
    assert kept == [rows[1], rows[3]], f"未申报集合不对: {kept}"
    assert net.undeclared_attempts([]) == []
    assert net.undeclared_attempts([{"test": declared}]) == []
    assert len(net.DECLARED_EGRESS_PROBES) == 1, (
        "申报白名单只许有绊线自检那一条，多一条就是掩耳盗铃"
    )


EGRESS_PROBE_SOURCE = """
import socket


def test_synthetic_egress_is_observable():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect(("192.0.2.1", 9))
    except OSError:
        pass
    finally:
        sock.close()
"""


def test_undeclared_egress_is_named_by_loaded_plugin(tmp_path):
    """哨兵必须有牙齿：插件按 -p 装载后，未申报的真出网在报告里被点名。

    跑在隔离子进程里，两个原因：① 只有 `-p network_egress_tripwire` 才是门禁审计的
    真实口径（顺带证明插件可装载、报告路径可写）；② 若在父进程里出网，会话台账会
    多一条未申报记录，把「全量 undeclared == 0」的证据自相矛盾地打脏。
    子进程用独立临时 LANTAI_HOME、剥密钥，只跑 tmp 里这一个文件，不碰仓库 conftest。
    """
    workdir = tmp_path / "probe"
    workdir.mkdir()
    (workdir / "test_zz_egress_probe.py").write_text(EGRESS_PROBE_SOURCE, encoding="utf-8")
    report = tmp_path / "egress.json"
    support = str(Path(__file__).resolve().parent / "support")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [support] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env["LANTAI_GATE_EGRESS_REPORT"] = str(report)
    env["LANTAI_HOME"] = str(tmp_path / "home")
    for key in (
        "API_KEY",
        "OPENAI_API_KEY",
        "RERANKER_API_KEY",
        "OPENAI_BASE_URL",
        "RERANKER_BASE_URL",
        "LANTAI_MCP_PROFILE",
        "MCP_TOOL_PROFILE",
        "LANTAI_GATE_REPORT",
        "LANTAI_TEST_HARNESS_AUDIT",
    ):
        env.pop(key, None)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_zz_egress_probe.py",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "network_egress_tripwire",
        ],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    detail = proc.stdout[-800:] + proc.stderr[-800:]
    assert report.exists(), f"插件没落报告 = 未取证 exit={proc.returncode} {detail}"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["armed"] is True, f"绊线未装载: {data}"
    assert data["count"] == 1, f"未申报出网没被记到台账: {data} {detail}"
    assert data["undeclared"] == 1, f"出网未被申报哨兵判为违规: {data}"
    attempt = data["attempts"][0]
    assert attempt["test"].endswith("test_zz_egress_probe.py::test_synthetic_egress_is_observable")
    assert attempt["kind"] == "remote"
    assert attempt["host"] == REMOTE_UNROUTABLE
