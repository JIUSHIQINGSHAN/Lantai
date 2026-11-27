"""Real stdio client and loopback HTTP peer for the remote MCP bridge."""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "lantai" / "cli" / "mcp_bridge.py"
TOOLS = {
    "search",
    "add",
    "feedback",
    "backfill",
    "add_dialogue",
    "candidates_pending",
    "candidate_review",
    "raw_add",
    "rollback",
    "conflicts_list",
    "conflict_resolve",
    "get_digest",
    "list_memories",
    "get_core_memory",
    "put_core_memory",
    "list_edges",
    "create_edge",
    "usage",
    "recall_report",
    "write_checkpoint",
    "latest_checkpoint",
    "list_memory_checkpoints",
}
TOKEN = "isolated-bridge-test-token"


@pytest.fixture
def peer():
    requests = []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_request(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(payload) if payload else None
            requests.append((self.command, self.path, dict(self.headers.items()), body))
            query = body.get("query") if isinstance(body, dict) else None
            status, data = 200, {"sample": "中文", "ok": True}
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/checkpoint/latest":
                data = {"session_id": "latest-session", "blocks": {"now": "demo"}}
            elif parsed.path == "/checkpoint" and "session_id" in params:
                data = {"session_id": params["session_id"][0], "blocks": {"now": "demo"}}
            elif parsed.path == "/checkpoint" and "memory_id" in params:
                data = {"checkpoints": [{"memory_id": params["memory_id"][0], "version": 2}]}
            if self.path == "/dialogue/async":
                data = {"status": "queued", "task_id": "task_demo"}
            if query in ("401", "503"):
                status, data = int(query), {"secret": TOKEN}
            elif query == "redirect":
                self.send_response(302)
                self.send_header("Location", "/credential-must-not-follow")
                self.end_headers()
                return
            elif query == "timeout":
                time.sleep(0.7)
            self.send_response(status)
            self.end_headers()
            with suppress(OSError):
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        do_GET = do_POST = do_PUT = handle_request  # noqa: N815 (HTTP server dispatch contract)

    class PeerServer(ThreadingHTTPServer):
        daemon_threads = False
        block_on_close = True

    with PeerServer(("127.0.0.1", 0), Peer) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{http.server_port}", requests
        finally:
            http.shutdown()
            thread.join(3)
            assert not thread.is_alive(), "桥接测试服务线程未结束"


def frame(req_id, method, **params):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def call(req_id, name, **arguments):
    return frame(req_id, "tools/call", name=name, arguments=arguments)


def run_bridge(url, *frames, allow_http=True):
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR"}
    }
    env.update(
        LANTAI_BRIDGE_BASE_URL=url,
        LANTAI_BRIDGE_BEARER_TOKEN=TOKEN,
        LANTAI_BRIDGE_ALLOW_LOOPBACK_HTTP="1" if allow_http else "0",
        LANTAI_BRIDGE_TIMEOUT_S="0.3",
    )
    lines = [f if isinstance(f, str) else json.dumps(f, ensure_ascii=False) for f in frames]
    return subprocess.run(
        [sys.executable, "-I", "-B", str(BRIDGE)],
        input=("\n".join(lines) + "\n").encode(),
        capture_output=True,
        env=env,
        timeout=10,
    )


def read_replies(proc):
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert all(response.get("jsonrpc") == "2.0" for response in responses)
    assert len({response["id"] for response in responses}) == len(responses)
    return {response["id"]: response for response in responses}


def test_discovery_and_session_attribution(peer):
    url, requests = peer
    replies = read_replies(
        run_bridge(
            url,
            frame(1, "initialize", protocolVersion="2024-11-05"),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            frame(2, "tools/list"),
            call(3, "search", query="我以前说的茶是什么？", session_id="session-example"),
            call(
                4,
                "add_dialogue",
                text="试点测试",
                session_id="session-example",
                turn=3,
                idempotency_key="message-3",
            ),
            call(5, "latest_checkpoint", session_id="session-example"),
            call(6, "create_edge", src_id="mem_a", dst_id="mem_b", relation="supports"),
        )
    )
    assert set(replies) == set(range(1, 7))
    assert replies[1]["result"]["protocolVersion"] == "2024-11-05"
    listed = {t["name"]: t for t in replies[2]["result"]["tools"]}
    assert set(listed) == TOOLS
    assert "session_id" in listed["search"]["inputSchema"]["properties"]
    assert json.loads(replies[3]["result"]["content"][0]["text"])["sample"] == "中文"
    headers = {k.lower(): v for k, v in requests[0][2].items()}
    assert headers["x-session-id"] == "session-example"
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert "session_id" not in requests[0][3]
    assert requests[1][1] == "/dialogue/async"
    assert requests[1][3]["turn"] == 3
    assert requests[1][3]["idempotency_key"] == "message-3"
    assert json.loads(replies[4]["result"]["content"][0]["text"])["status"] == "queued"
    assert requests[2][1] == "/checkpoint?session_id=session-example"
    assert requests[3][3] == {
        "source_memory_id": "mem_a",
        "target_memory_id": "mem_b",
        "relation": "supports",
    }


def test_conflict_resolution_rejects_invalid_input_before_http(peer):
    url, requests = peer
    invalid = [
        {"event_id": "event-1", "decision": "resolved"},
        {"event_id": "event-1", "decision": "resolved", "note": ""},
        {"event_id": "event-1", "decision": "dismissed", "note": " \t\n"},
        {"event_id": "event-1", "decision": "resolved", "note": "\u3000\u00a0"},
        {"event_id": "event-1", "decision": "unknown", "note": "reason"},
        {"event_id": "event-1", "decision": 1, "note": "reason"},
    ]
    replies = read_replies(
        run_bridge(
            url,
            *(call(i, "conflict_resolve", **args) for i, args in enumerate(invalid, 1)),
            call(7, "conflict_resolve", event_id="event-1", decision="resolved", note="reason"),
            call(8, "conflict_resolve", event_id="event-2", decision="dismissed", note="理由"),
        )
    )
    for request_id in range(1, 7):
        assert replies[request_id]["error"]["code"] == -32602
    assert len(requests) == 2
    assert requests[0][3] == {"decision": "resolved", "note": "reason"}
    assert requests[1][3] == {"decision": "dismissed", "note": "理由"}


def test_checkpoint_history_tool_preserves_legacy_latest_arguments(peer):
    url, requests = peer
    replies = read_replies(
        run_bridge(
            url,
            call(1, "latest_checkpoint"),
            call(2, "latest_checkpoint", session_id="session-example"),
            call(3, "list_memory_checkpoints", memory_id="mem-A", limit=2),
            call(4, "latest_checkpoint", memory_id="mem-A", limit=2),
            call(5, "list_memory_checkpoints"),
            call(6, "latest_checkpoint", memory_id="mem-A", session_id="session-example"),
        )
    )

    def value(request_id):
        return json.loads(replies[request_id]["result"]["content"][0]["text"])

    assert value(1)["session_id"] == "latest-session"
    assert value(2)["session_id"] == "session-example"
    assert value(3) == value(4) == {"checkpoints": [{"memory_id": "mem-A", "version": 2}]}
    assert replies[5]["error"]["code"] == replies[6]["error"]["code"] == -32602
    assert len(requests) == 4
    for request in requests[2:]:
        assert parse_qs(urlsplit(request[1]).query) == {"memory_id": ["mem-A"], "limit": ["2"]}


def test_failures_are_tools_errors_and_never_follow_redirect(peer):
    url, requests = peer
    proc = run_bridge(
        url,
        "{bad json",
        "[]",
        *(
            call(i, "search", query=q)
            for i, q in enumerate(("401", "503", "redirect", "timeout"), 1)
        ),
        call(5, "search", query="test", session_id="bad\nheader"),
        frame(6, "ping"),
    )
    replies = read_replies(proc)
    for i, label in enumerate(("401", "503", "302", "timeout"), 1):
        assert replies[i]["result"]["isError"] is True
        assert label in replies[i]["result"]["content"][0]["text"].lower()
    assert TOKEN.encode() not in proc.stdout + proc.stderr
    assert replies[5]["error"]["code"] == -32602
    assert replies[6]["result"] == {}
    assert [r[1] for r in requests] == ["/search"] * 4


@pytest.mark.parametrize(
    "url, allow_http",
    [
        ("http://127.0.0.1:1", False),
        ("http://192.0.2.1:1", True),
        ("https://user:secret@localhost", True),
    ],
)
def test_rejects_insecure_configuration(url, allow_http):
    proc = run_bridge(url, frame(1, "ping"), allow_http=allow_http)
    assert proc.returncode != 0
    assert b"LANTAI_BRIDGE_BASE_URL" in proc.stderr
    assert b"secret" not in proc.stderr and TOKEN.encode() not in proc.stderr


def test_concurrent_bridge_clients_keep_session_headers_per_request(peer):
    url, requests = peer

    def invoke(label):
        replies = read_replies(
            run_bridge(
                url,
                call(1, "search", query=f"{label}-attributed", session_id=label),
                call(2, "search", query=f"{label}-unattributed"),
                frame(3, "ping"),
            )
        )
        assert set(replies) == {1, 2, 3}
        assert replies[3]["result"] == {}
        assert all("error" not in reply for reply in replies.values())

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(invoke, ("session-A", "session-B")))

    assert len(requests) == 4
    by_query = {body["query"]: (headers, body) for _, _, headers, body in requests}
    assert set(by_query) == {
        "session-A-attributed",
        "session-A-unattributed",
        "session-B-attributed",
        "session-B-unattributed",
    }
    for label in ("session-A", "session-B"):
        for suffix in ("attributed", "unattributed"):
            headers, body = by_query[f"{label}-{suffix}"]
            headers = {key.lower(): value for key, value in headers.items()}
            assert headers.get("x-session-id") == (label if suffix == "attributed" else None)
            assert "session_id" not in body
            assert headers["authorization"] == f"Bearer {TOKEN}"


def test_invalid_bridge_frames_never_reach_http_and_next_request_survives(peer):
    url, requests = peer
    replies = read_replies(
        run_bridge(
            url,
            {"jsonrpc": "2.0", "id": 1, "method": None},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": []},
            frame(3, "tools/call", name="search", arguments=[]),
            call(4, "search", query="test", top_k=True),
            call(5, "shishi_vault"),
            frame(6, "ping"),
        )
    )
    assert set(replies) == {1, 2, 3, 4, 5, 6}
    assert replies[1]["error"]["code"] == -32600
    assert all(replies[index]["error"]["code"] == -32602 for index in (2, 3, 4, 5))
    assert replies[6]["result"] == {}
    assert requests == []
