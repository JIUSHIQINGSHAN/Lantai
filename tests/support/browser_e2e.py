"""Real loopback ASGI server and Playwright Chromium, with bounded cleanup."""

from __future__ import annotations

import html
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Page, sync_playwright

from tests.support.ui_browser import chromium_executable


def install_mcp_host(app: FastAPI) -> None:
    """Test host only: real sandbox messages are forwarded to the real MCP dispatcher."""
    from fastapi.responses import HTMLResponse

    from lantai.cli.mcp import handle
    from lantai.mcp_app.protocol import MCP_APP_RESOURCE_URI, read_app_resource
    from lantai.mcp_apps.xuanjing import RESOURCE_URI, read_xuanjing_app_resource

    @app.post("/__test_tools__")
    def tool_call(frame: dict) -> dict | None:
        return handle(frame)

    @app.get("/__test_host__", response_class=HTMLResponse)
    def host(kind: str = "xuanjing", server_tools: bool = True) -> str:
        resource = (
            read_xuanjing_app_resource(RESOURCE_URI)["contents"][0]["text"]
            if kind == "xuanjing"
            else read_app_resource(MCP_APP_RESOURCE_URI)["text"]
        )
        origin = "get_xuanjing_graph" if kind == "xuanjing" else "core_memory_get"
        return (
            """<!doctype html><html><meta charset="utf-8">
<style>html,body{margin:0;height:100%}iframe{width:100%;height:100%;border:0}</style>
<iframe title="MCP App" sandbox="allow-scripts" srcdoc="""
            + '"'
            + html.escape(resource, quote=True)
            + '"'
            + """></iframe>
<script>
const originTool = """
            + json.dumps(origin)
            + ";\nconst serverTools = "
            + json.dumps(server_tools)
            + """;
const app = document.querySelector('iframe');
window.__calls = [];
window.__hostError = null;
window.__hostResult = null;
const send = (frame) => app.contentWindow.postMessage({jsonrpc:'2.0', ...frame}, '*');
window.sendContext = (context, nested=true) => send({method:'ui/notifications/host-context-changed',
  params: nested ? {hostContext:context} : context});
window.sendResult = (result) => send({method:'ui/notifications/tool-result', params:result});
async function call(frame) {
  window.__calls.push(frame.params);
  const response = await fetch('/__test_tools__', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(frame)});
  return response.json();
}
window.addEventListener('message', async (event) => {
  if (event.source !== app.contentWindow || event.data?.jsonrpc !== '2.0') return;
  const message = event.data;
  if (message.method === 'ui/initialize') {
    send({id:message.id, result:{protocolVersion:'2026-01-26',
      hostCapabilities:serverTools ? {serverTools:{}} : {},
      hostContext:{theme:'light', toolInfo:{tool:{name:originTool}},
        styles:{variables:{'--color-background-primary':'#f8fafb'}}}}});
  } else if (message.method === 'ui/notifications/initialized') {
    send({method:'ui/notifications/tool-input', params:{arguments:{}}});
    if (originTool === 'get_xuanjing_graph') {
      const reply = await call({jsonrpc:'2.0', id:0, method:'tools/call',
        params:{name:originTool, arguments:{limit:250}}});
      send({method:'ui/notifications/tool-result', params:reply.result});
    }
  } else if (message.method === 'tools/call') {
    if (window.__hostError) {
      window.__calls.push(message.params);
      send({id:message.id, error:{code:-32603, message:window.__hostError}});
      window.__hostError = null;
    } else if (window.__hostResult) {
      window.__calls.push(message.params);
      send({id:message.id, result:window.__hostResult});
      window.__hostResult = null;
    } else send(await call(message));
  }
});
</script></html>"""
        )


@contextmanager
def live_server(app: FastAPI) -> Iterator[str]:
    # Schema and fixtures are prepared by the caller; no production startup workers.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="off",
            log_level="error",
            timeout_graceful_shutdown=5,
        )
        server = uvicorn.Server(config)
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "Loopback test server did not start"
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), "Loopback test server did not stop"


@contextmanager
def browser_page(tmp_path: Path, url: str, *, width: int = 1440) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chromium_executable())
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            yield page
            assert errors == [], f"Uncaught browser errors: {errors}"
        except BaseException:
            page.screenshot(path=str(tmp_path / "browser-failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()
