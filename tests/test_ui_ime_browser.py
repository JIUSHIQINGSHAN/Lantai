"""真实 Chromium 回归：组合输入确认键不能触发控制台动作。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.support.ui_browser import chromium

_UI_DIR = Path(__file__).resolve().parents[1] / "lantai" / "api" / "ui"


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
window.__releaseResponses = null;
const responsesReady = new Promise(resolve => { window.__releaseResponses = resolve; });
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
  await responsesReady;
  if (path === '/terminal/chat') return new Response('', {status: 200, headers: {'Content-Type': 'text/event-stream'}});
  return new Response(JSON.stringify({results: [], memories: [], probes: []}), {
    status: 200,
    headers: {'Content-Type': 'application/json'}
  });
};
window.sessionStorage.clear();
window.localStorage.clear();
window.__result = () => ({requests: window.__requests, events: window.__events, errors: window.__errors,
  values: ['#terminalInput', '#playgroundQuery', '#vaultSearchInput'].map(id => document.querySelector(id).value)});
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
]).then(() => { window.__triggered = true; }).catch(error => {
  window.__errors.push(String(error));
  window.__triggered = true;
});
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


async def _completed_result(browser) -> dict:
    await browser.wait(
        "!document.querySelector('#terminalSendBtn').disabled && "
        "document.querySelector('#chatFeed .agent') && "
        "!document.querySelector('#playgroundSearchBtn').disabled && "
        "document.querySelector('#playgroundResults').textContent.includes('零召回') && "
        "document.querySelector('#vaultList').textContent.includes('档案库暂无匹配记录')"
    )
    return await browser.evaluate("window.__result()")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["composing", "fallback", "other"])
async def test_composing_enter_does_not_dispatch_any_console_action(
    tmp_path: Path, ime_harness_server: str, mode: str
) -> None:
    async with chromium(
        tmp_path, ime_harness_server + "?mode=" + mode, ready="window.__triggered === true"
    ) as browser:
        result = await browser.evaluate("window.__result()")
        assert result["errors"] == [], result
        assert result["requests"] == [], result
        assert result["values"] == ["中文候选"] * 3
        assert not any(row["prevented"] for row in result["events"])
        await browser.evaluate("window.__releaseResponses()")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "button"])
async def test_confirmed_text_submits_once_per_action(
    tmp_path: Path, ime_harness_server: str, mode: str
) -> None:
    async with chromium(
        tmp_path, ime_harness_server + "?mode=" + mode, ready="window.__triggered === true"
    ) as browser:
        assert await browser.evaluate(
            "document.querySelector('#terminalSendBtn').disabled && "
            "document.querySelector('#playgroundSearchBtn').disabled"
        )
        await browser.evaluate("window.__releaseResponses()")
        result = await _completed_result(browser)
        assert result["errors"] == [], result
        assert sorted(row["path"] for row in result["requests"]) == [
            "/probing/detect",
            "/search",
            "/search",
            "/terminal/chat",
        ]
        assert all(row["query"] == "中文候选" for row in result["requests"])
        assert result["values"] == ["", "中文候选", "中文候选"]
