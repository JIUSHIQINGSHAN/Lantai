"""真实 Chromium 回归：组合输入确认键不能触发控制台动作。"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_UI_DIR = Path(__file__).resolve().parents[1] / "lantai" / "api" / "ui"


def _chromium_executable() -> str:
    configured = os.environ.get("LANTAI_TEST_CHROMIUM")
    if configured:
        assert Path(configured).is_file(), "LANTAI_TEST_CHROMIUM must name a browser executable"
        return configured
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        executable = shutil.which(name)
        if executable:
            return executable
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache")))
    cached = sorted(
        (local / "ms-playwright").glob(
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"
        )
    )
    for executable in [*cached, Path("C:/Program Files/Google/Chrome/Application/chrome.exe")]:
        if executable.is_file():
            return str(executable)
    pytest.fail("Chromium is required; set LANTAI_TEST_CHROMIUM")


_HARNESS = """
<!doctype html>
<meta charset="utf-8">
<div id="chatFeed"></div>
<input id="terminalInput" value="中文候选">
<button id="terminalSendBtn" type="button">发送</button>
<button id="graphLoadAllBtn" type="button">图谱</button>
<input id="playgroundQuery" value="中文候选">
<select id="playgroundDomain"><option value="all">全部</option></select>
<button id="playgroundSearchBtn" type="button">检索</button>
<div id="playgroundResults"></div>
<div id="probingAlertBox" hidden><span id="probeQuestionText"></span></div>
<input id="vaultSearchInput" value="中文候选">
<select id="vaultDomainFilter"><option value="">全部</option></select>
<div id="vaultList"></div>
<button id="vaultSearchBtn" type="button">档案</button>
<script>
window.__requests = [];
window.__events = [];
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(e.message));
window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
const mode = new URLSearchParams(location.search).get('mode') || 'composing';
const trigger = selector => {
  const input = document.querySelector(selector);
  if (mode === 'button') {
    const id = {'#terminalInput': '#terminalSendBtn', '#playgroundQuery': '#playgroundSearchBtn', '#vaultSearchInput': '#vaultSearchBtn'}[selector];
    document.querySelector(id).click();
    return;
  }
  const event = new KeyboardEvent('keydown', {
    key: mode === 'other' ? 'ArrowDown' : 'Enter',
    keyCode: mode === 'fallback' ? 229 : 13,
    bubbles: true, cancelable: true, isComposing: mode === 'composing'
  });
  input.dispatchEvent(event);
  window.__events.push({selector, prevented: event.defaultPrevented});
};
window.fetch = async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  window.__requests.push({path, query: body && body.query});
  if (!['/terminal/chat', '/search', '/probing/detect'].includes(path)) {
    throw new Error('Unexpected endpoint: ' + path);
  }
  if (path === '/terminal/chat') return new Response('', {status: 200, headers: {'Content-Type': 'text/event-stream'}});
  return new Response(JSON.stringify({results: [], memories: [], probes: []}), {
    status: 200,
    headers: {'Content-Type': 'application/json'}
  });
};
window.sessionStorage.clear();
window.localStorage.clear();
const report = async () => {
  await new Promise(resolve => setTimeout(resolve, 250));
  const out = document.createElement('pre');
  out.id = 'ime-result';
  out.textContent = JSON.stringify({requests: window.__requests, events: window.__events, errors: window.__errors,
    values: ['#terminalInput', '#playgroundQuery', '#vaultSearchInput'].map(id => document.querySelector(id).value)});
  document.body.append(out);
};
Promise.all([
  import('/ui/assets/terminal.js').then(({initTerminal}) => {
    initTerminal();
    trigger('#terminalInput');
  }),
  import('/ui/assets/playground.js').then(({initPlayground}) => {
    initPlayground();
    trigger('#playgroundQuery');
  }),
  import('/ui/assets/vault.js').then(({initVault}) => {
    initVault();
    trigger('#vaultSearchInput');
  })
]).then(report);
</script>
"""


@pytest.fixture()
def ime_harness_server() -> Iterator[str]:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(_UI_DIR), **kwargs)

        def do_GET(self):  # noqa: N802
            if urlsplit(self.path).path == "/ime-harness":
                payload = _HARNESS.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.path = self.path.removeprefix("/ui/assets")
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ime-harness"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _run_harness(tmp_path: Path, url: str) -> dict:
    proc = subprocess.run(
        [
            _chromium_executable(),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-background-networking",
            "--force-device-scale-factor=1",
            "--virtual-time-budget=1500",
            f"--user-data-dir={tmp_path / 'chromium-profile'}",
            "--dump-dom",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    match = re.search(r'<pre id="ime-result">(.*?)</pre>', proc.stdout, flags=re.S)
    assert match, html.escape(proc.stdout[-4000:])
    return json.loads(html.unescape(match.group(1)))


@pytest.mark.parametrize("mode", ["composing", "fallback", "other"])
def test_composing_enter_does_not_dispatch_any_console_action(
    tmp_path: Path, ime_harness_server: str, mode: str
) -> None:
    result = _run_harness(tmp_path, ime_harness_server + "?mode=" + mode)
    assert result["errors"] == [], result
    assert result["requests"] == [], result
    assert result["values"] == ["中文候选"] * 3
    assert not any(row["prevented"] for row in result["events"])


@pytest.mark.parametrize("mode", ["normal", "button"])
def test_confirmed_text_submits_once_per_action(
    tmp_path: Path, ime_harness_server: str, mode: str
) -> None:
    result = _run_harness(tmp_path, ime_harness_server + "?mode=" + mode)
    assert result["errors"] == [], result
    assert sorted(row["path"] for row in result["requests"]) == [
        "/probing/detect",
        "/search",
        "/search",
        "/terminal/chat",
    ]
    assert all(row["query"] == "中文候选" for row in result["requests"])
    assert result["values"] == ["", "中文候选", "中文候选"]
