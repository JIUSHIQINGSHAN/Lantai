"""Isolated product UI server and Chromium CDP driver; no business database or network."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets import connect

UI_DIR = Path(__file__).resolve().parents[2] / "lantai" / "api" / "ui"


def chromium_executable() -> str:
    configured = os.environ.get("LANTAI_TEST_CHROMIUM")
    if configured:
        if not Path(configured).is_file():
            raise RuntimeError("LANTAI_TEST_CHROMIUM must name a browser executable")
        return configured
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        executable = shutil.which(name)
        if executable:
            return executable
    if sys.platform == "win32":
        cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        default_cache = cache / "ms-playwright"
        system = [Path("C:/Program Files/Google/Chrome/Application/chrome.exe")]
        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-win*/chrome-headless-shell.exe",
            "chromium-*/chrome-win*/chrome.exe",
        )
    elif sys.platform == "darwin":
        default_cache = Path.home() / "Library/Caches/ms-playwright"
        system = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell",
            "chromium-*/chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        )
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        default_cache = cache / "ms-playwright"
        system = []
        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
            "chromium-*/chrome-linux*/chrome",
        )
    configured_cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    # Playwright's special value 0 is package-local, not a filesystem cache path.
    caches = [Path(configured_cache)] if configured_cache and configured_cache != "0" else []
    caches.append(default_cache)
    for root in caches:
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern), reverse=True):
                if candidate.is_file():
                    return str(candidate)
    for candidate in system:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Chromium required: install Chrome/Chromium or set LANTAI_TEST_CHROMIUM")


ITEM = {
    "id": "proposal:synthetic",
    "source_id": "synthetic",
    "kind": "proposal",
    "title": "测试提案",
    "summary": "合成记录",
    "reason": "测试",
    "risk": "low",
    "status": "pending",
    "priority": 1,
    "allowed_actions": ["approve", "reject"],
}


RECALL_REPORT = {"window_days": 7, "real": 4, "zero": 1, "zero_recall_rate": 0.25}


@contextmanager
def product_server(
    *, recall_gate: threading.Event | None = None, recall_status: int = 200
) -> Iterator[tuple[str, list[tuple], list[tuple]]]:
    """Serve real UI assets with an explicit, synthetic API allowlist."""
    requests = []
    unexpected = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, payload, content_type="application/json", status=200):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/ui":
                self.respond((UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            if path.startswith("/ui/assets/"):
                name = path.removeprefix("/ui/assets/")
                if "/" not in name and "\\" not in name and (UI_DIR / name).is_file():
                    kind = "text/css" if name.endswith(".css") else "text/javascript"
                    self.respond((UI_DIR / name).read_bytes(), kind)
                    return
            requests.append(("GET", self.path, None))
            if path == "/work-items":
                items = (
                    [dict(ITEM)]
                    if parse_qs(parsed.query).get("section") == ["pending_decisions"]
                    else []
                )
                self.respond({"items": items, "total": len(items)})
            elif path == "/work-items/detail/proposal/synthetic":
                self.respond(
                    {
                        "item": dict(ITEM),
                        "source": {"proposed_patch": {"content": "新内容"}},
                        "related": {},
                    }
                )
            elif path == "/monitor/overview":
                self.respond({"summary": {"alert_count": 0}})
            elif path == "/persona":
                self.respond({"name": "测试人格"})
            elif path == "/persona/list":
                self.respond({"items": []})
            elif path == "/scratchpad":
                self.respond({"content": ""})
            elif path == "/evolution/consolidate/report":
                self.respond({"status": "idle"})
            elif path == "/retrieval/recall-report":
                if recall_gate is not None and not recall_gate.wait(timeout=10):
                    self.respond({"detail": "Test did not release recall response"}, status=500)
                    return
                payload = (
                    RECALL_REPORT
                    if recall_status == 200
                    else {"detail": "synthetic recall failure"}
                )
                self.respond(payload, status=recall_status)
            elif path == "/prompts":
                self.respond({"prompts": []})
            elif path == "/core-memory":
                self.respond({"blocks": []})
            elif path == "/favicon.ico":
                self.respond(b"", "image/x-icon")
            else:
                unexpected.append(("GET", self.path))
                self.respond({"detail": "Unexpected test endpoint"}, status=500)

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            requests.append(("POST", self.path, body))
            if self.path == "/proposals/synthetic/decide":
                self.respond({"ok": True})
            else:
                unexpected.append(("POST", self.path))
                self.respond({"detail": "Unexpected test endpoint"}, status=500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ui", requests, unexpected
    finally:
        if recall_gate is not None:
            recall_gate.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class CDP:
    """Sequential CDP commands and keyboard input for a single isolated tab."""

    def __init__(self, socket: Any) -> None:
        self.socket = socket
        self.counter = 0
        self.session = None
        self.script_errors: list[dict] = []

    async def send(self, method: str, params: dict | None = None, *, browser: bool = False) -> dict:
        self.counter += 1
        request = {"id": self.counter, "method": method, "params": params or {}}
        if self.session and not browser:
            request["sessionId"] = self.session
        await self.socket.send(json.dumps(request))
        while True:
            response = json.loads(await asyncio.wait_for(self.socket.recv(), timeout=15))
            if response.get("method") == "Runtime.exceptionThrown":
                self.script_errors.append(response["params"])
            if response.get("id") == request["id"]:
                if "error" in response:
                    raise AssertionError(response["error"])
                return response.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        """Evaluate a UI action or observation and surface script failures."""
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        )
        assert "exceptionDetails" not in result, result
        return result["result"].get("value")

    async def wait(self, expression: str) -> bool:
        """Wait for observable UI state and its queued render events, bounded to five seconds."""
        return await self.evaluate(
            """new Promise((resolve, reject) => {
          const deadline = performance.now() + 5000;
          const check = () => {
            if (EXPRESSION) return requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)));
            if (performance.now() > deadline) return reject(new Error('UI condition timed out'));
            requestAnimationFrame(check);
          }; check();
        })""".replace("EXPRESSION", expression)
        )

    async def key(self, key: str, code: str, number: int, modifiers: int = 0) -> None:
        """Send a real browser key press and release."""
        params = {"key": key, "code": code, "windowsVirtualKeyCode": number, "modifiers": modifiers}
        text = {"text": "\r"} if key == "Enter" else {}
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", **params, **text})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **params})


@asynccontextmanager
async def chromium(
    tmp_path: Path,
    url: str,
    width: int = 1440,
    *,
    ready: str = "document.querySelector('.work-row')",
) -> AsyncIterator[CDP]:
    """Launch a fresh browser profile and load product scripts without user data."""
    process = await asyncio.create_subprocess_exec(
        chromium_executable(),
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-background-networking",
        "--no-proxy-server",
        "--remote-debugging-port=0",
        f"--user-data-dir={tmp_path / 'browser-profile'}",
        "about:blank",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            while True:
                line = await process.stderr.readline()
                if not line:
                    raise RuntimeError("Chromium exited before opening CDP")
                if b"DevTools listening on " in line:
                    endpoint = line.decode().split("DevTools listening on ", 1)[1].strip()
                    break
        async with connect(endpoint, max_size=8 * 1024 * 1024) as socket:
            cdp = CDP(socket)
            target = await cdp.send("Target.createTarget", {"url": "about:blank"}, browser=True)
            attached = await cdp.send(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
                browser=True,
            )
            cdp.session = attached["sessionId"]
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            )
            await cdp.send("Page.navigate", {"url": url})
            await cdp.wait(ready)
            yield cdp
            await cdp.send("Runtime.evaluate", {"expression": "0"})
            assert cdp.script_errors == [], cdp.script_errors
            await cdp.send("Browser.close", browser=True)
    finally:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
        await process.wait()
