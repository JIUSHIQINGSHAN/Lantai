"""Isolated HTTP fixtures and CDP input for the knowledge review UI smoke tests.

Only the API boundary is synthetic: HTML, CSS and ES modules are served unmodified.
No product server, database, credentials or user browser profile is used.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import socket
import threading
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from websockets import connect

UI = Path(__file__).resolve().parents[2] / "lantai" / "api" / "ui"
CONTENT = '<img src=x onerror="window.injected=true">知识候选正文'
READY_ID = "knowledge_candidate:mem_ready"
EMPTY_ID = "knowledge_candidate:mem_empty"


def browser_path():
    configured = os.environ.get("LANTAI_TEST_CHROMIUM")
    if configured:
        if not Path(configured).is_file():
            raise RuntimeError("LANTAI_TEST_CHROMIUM must name a Chromium executable")
        return configured
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        if path := shutil.which(name):
            return path
    path = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    return str(path) if path.is_file() else None


def item(source_id, kind="knowledge_candidate"):
    return {
        "id": f"{kind}:{source_id}",
        "source_id": source_id,
        "kind": kind,
        "title": f"待审记录 {source_id}",
        "summary": "合成知识候选",
        "reason": "等待人工审核",
        "risk": "medium",
        "status": "candidate",
        "priority": "high",
        "allowed_actions": ["approve", "reject"],
    }


class ReviewScenario:
    def __init__(self):
        self.requests = []
        self.unexpected = []
        self.outcomes = []
        self.version = 3
        self.blocked = False
        self.removed = set()
        self.hold = None
        self.receipts = {}
        self.low_quality_recommended = False

    @property
    def posts(self):
        return [request for request in self.requests if request[0] == "POST"]

    def detail(self, source_id):
        empty = source_id == "mem_empty"
        evidence = (
            []
            if empty
            else [
                {
                    "id": f"ev_{i}",
                    "source_memory_id": f"source_{i}",
                    "content": f"证据 {i} <script>window.injected=true</script>",
                    "evidence_type": "observation",
                    "reliability": 0.95,
                    "independence": 1,
                    "status": "active",
                    "source_memory_status": "active",
                    "source_memory_version": 2,
                    "eligible": i < 3,
                    "blocking_reasons": [] if i < 3 else ["evidence reliability below threshold"],
                }
                for i in range(1, 4)
            ]
        )
        if self.low_quality_recommended:
            for row in evidence:
                row.update(reliability=0.7, eligible=True, blocking_reasons=[])
                if row["id"] == "ev_3":
                    row["independence"] = 0.1
        blocked = empty or self.blocked or self.low_quality_recommended
        reasons = (
            ["select stored evidence linked to this candidate"]
            if empty
            else ["candidate confidence below threshold"]
            if self.blocked
            else ["evidence quality below threshold"]
            if self.low_quality_recommended
            else []
        )
        return {
            "item": item(source_id),
            "source": {
                "id": source_id,
                "content": CONTENT,
                "version": self.version,
                "status": "candidate",
                "source_ids": ["source_1", "source_2"],
                "provenance": {"origin": "synthetic"},
            },
            "related": {
                "evidence": evidence,
                "review": {
                    "eligible": not blocked,
                    "blocking_reasons": reasons,
                    "confidence": 0.2 if self.blocked else 0.95,
                    "evidence_quality": 0
                    if empty
                    else 0.49
                    if self.low_quality_recommended
                    else 0.95,
                    "independent_sources": 0 if empty else 3 if self.low_quality_recommended else 2,
                    "min_confidence": 0.7,
                    "min_evidence_quality": 0.55 if self.low_quality_recommended else 0.7,
                    "min_sources": 2,
                    "evidence_ids": [] if empty else ["ev_1", "ev_2"],
                    "recommended_evidence_ids": [] if empty else ["ev_1", "ev_2", "ev_3"],
                    "expected_version": self.version,
                },
            },
        }


@contextmanager
def review_server():
    scenario = ReviewScenario()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, data, status=200, mime="application/json"):
            content = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            parts = urlsplit(self.path)
            path = parts.path
            if path == "/ui":
                self.reply((UI / "index.html").read_bytes(), mime="text/html; charset=utf-8")
                return
            if path.startswith("/ui/assets/"):
                name = path.removeprefix("/ui/assets/")
                if "/" not in name and "\\" not in name and (UI / name).is_file():
                    mime = "text/css" if name.endswith(".css") else "text/javascript"
                    self.reply((UI / name).read_bytes(), mime=mime)
                    return
            scenario.requests.append(("GET", self.path, None))
            if path == "/work-items":
                query = parse_qs(parts.query)
                values = [item("mem_ready"), item("mem_empty"), item("intake", "candidate")]
                values = [value for value in values if value["source_id"] not in scenario.removed]
                if query.get("section") != ["pending_decisions"]:
                    values = []
                if query.get("kind"):
                    values = [value for value in values if value["kind"] == query["kind"][0]]
                self.reply({"items": values, "total": len(values)})
            elif path.startswith("/work-items/detail/knowledge_candidate/"):
                source_id = path.rsplit("/", 1)[-1]
                if source_id in scenario.removed:
                    self.reply({"detail": "no longer pending"}, status=404)
                else:
                    self.reply(scenario.detail(source_id))
            elif path == "/monitor/overview":
                self.reply({"summary": {"alert_count": 0}})
            elif path in {
                "/persona",
                "/persona/list",
                "/scratchpad",
                "/evolution/consolidate/report",
                "/retrieval/recall-report",
                "/prompts",
                "/core-memory",
            }:
                self.reply(
                    {
                        "name": "测试",
                        "items": [],
                        "content": "",
                        "status": "idle",
                        "prompts": [],
                        "blocks": [],
                    }
                )
            elif path == "/favicon.ico":
                self.reply(b"", mime="image/x-icon")
            else:
                scenario.unexpected.append(("GET", self.path))
                self.reply({"detail": "unexpected fixture endpoint"}, status=500)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            scenario.requests.append(("POST", self.path, copy.deepcopy(body)))
            if not self.path.startswith("/knowledge-candidates/") or not self.path.endswith(
                "/review"
            ):
                scenario.unexpected.append(("POST", self.path))
                self.reply({"detail": "unexpected review endpoint"}, status=500)
                return
            if scenario.hold:
                scenario.hold.wait(10)
            source_id = self.path.split("/")[2]
            outcome = scenario.outcomes.pop(0) if scenario.outcomes else "success"
            if outcome == "409":
                scenario.version += 1
                self.reply(
                    {"detail": "candidate status/version changed; refresh before reviewing"}, 409
                )
                return
            if outcome == "422":
                scenario.blocked = True
                self.reply(
                    {
                        "detail": {
                            "code": "quality_gate",
                            "blocking_reasons": ["candidate confidence below threshold"],
                        }
                    },
                    422,
                )
                return
            if outcome == "503":
                self.reply(
                    {
                        "detail": {
                            "code": "commit_outcome_unknown",
                            "applied": None,
                            "retry_same_request": True,
                        }
                    },
                    503,
                )
                return
            receipt = scenario.receipts.get(body["request_id"])
            if receipt:
                self.reply({**receipt, "idempotent": True})
                return
            receipt = {
                "ok": True,
                "decision": body["decision"],
                "candidate_id": source_id,
                "candidate_status": "consolidated" if body["decision"] == "approve" else "archived",
                "version": body["expected_version"] + 1,
                "memory_id": "mem_knowledge" if body["decision"] == "approve" else None,
                "request_id": body["request_id"],
                "checkpoint_id": "ckpt_test",
                "idempotent": False,
            }
            scenario.receipts[body["request_id"]] = receipt
            scenario.removed.add(source_id)
            if outcome == "disconnect":
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.reply(receipt)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ui", scenario
    finally:
        if scenario.hold:
            scenario.hold.set()
        server.shutdown()
        server.server_close()
        worker.join(5)


class Browser:
    def __init__(self, ws):
        self.ws = ws
        self.serial = 0
        self.session = None
        self.errors = []
        self.loaded = False

    def event(self, response):
        if response.get("method") == "Runtime.exceptionThrown":
            self.errors.append(response["params"])
        if (
            response.get("method") == "Page.loadEventFired"
            and response.get("sessionId") == self.session
        ):
            self.loaded = True

    async def reload(self):
        self.loaded = False
        await self.command("Page.reload", {"ignoreCache": True})
        async with asyncio.timeout(15):
            while not self.loaded:
                self.event(json.loads(await self.ws.recv()))

    async def command(self, method, params=None, browser=False):
        self.serial += 1
        request = {"id": self.serial, "method": method, "params": params or {}}
        if self.session and not browser:
            request["sessionId"] = self.session
        await self.ws.send(json.dumps(request))
        while True:
            response = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
            self.event(response)
            if response.get("id") == self.serial:
                assert "error" not in response, response
                return response.get("result", {})

    async def js(self, expression):
        response = await self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        )
        assert "exceptionDetails" not in response, response
        return response["result"].get("value")

    async def wait(self, expression):
        await self.js(
            """new Promise((resolve, reject) => {
          const deadline = performance.now() + 5000;
          const check = () => {
            if (EXPRESSION) return requestAnimationFrame(() => requestAnimationFrame(resolve));
            if (performance.now() > deadline) return reject(new Error('UI state timeout: ' + MESSAGE));
            requestAnimationFrame(check);
          }; check();
        })""".replace("EXPRESSION", expression).replace("MESSAGE", json.dumps(expression))
        )

    async def click(self, selector):
        point = await self.js(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); el.scrollIntoView({{block: 'center', inline: 'nearest'}}); const r = el.getBoundingClientRect(); return {{x: r.x + r.width / 2, y: r.y + r.height / 2}}; }})()"
        )
        await self.command(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", **point, "button": "left", "clickCount": 1},
        )
        await self.command(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", **point, "button": "left", "clickCount": 1},
        )

    async def key(self, key, code, number):
        for kind in ("keyDown", "keyUp"):
            await self.command(
                "Input.dispatchKeyEvent",
                {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": number},
            )

    async def reason(self, text):
        await self.js(
            f"document.querySelector('#dialogBody textarea').value = {json.dumps(text)}; document.querySelector('#dialogBody textarea').dispatchEvent(new Event('input', {{bubbles: true}}))"
        )


@asynccontextmanager
async def review_browser(tmp_path, url, executable, width=1440):
    process = await asyncio.create_subprocess_exec(
        executable,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-background-networking",
        "--no-proxy-server",
        # A local DNS alias gives a real insecure HTTP context (loopback is secure).
        "--host-resolver-rules=MAP review.lantai.test 127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={tmp_path / 'knowledge-review-profile'}",
        "about:blank",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            while True:
                line = await process.stderr.readline()
                if b"DevTools listening on " in line:
                    endpoint = line.decode().split("DevTools listening on ", 1)[1].strip()
                    break
                if not line:
                    raise RuntimeError("Chromium closed before starting CDP")
        async with connect(endpoint, max_size=8 * 1024 * 1024) as ws:
            page = Browser(ws)
            target = await page.command("Target.createTarget", {"url": "about:blank"}, browser=True)
            attached = await page.command(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
                browser=True,
            )
            page.session = attached["sessionId"]
            await page.command("Runtime.enable")
            await page.command("Page.enable")
            await page.command(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            )
            version = await page.command("Browser.getVersion", browser=True)
            print("Browser:", version["product"], "viewport:", width)
            await page.command(
                "Page.navigate", {"url": url.replace("127.0.0.1", "review.lantai.test")}
            )
            await page.wait("document.querySelectorAll('.work-row').length === 3")
            assert await page.js("!isSecureContext && typeof crypto.randomUUID === 'undefined'")
            yield page
            assert page.errors == [], page.errors
            await page.command("Browser.close", browser=True)
    finally:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
        await process.wait()
