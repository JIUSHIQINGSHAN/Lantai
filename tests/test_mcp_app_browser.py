"""Real Chromium coverage for the MCP App sandbox message bridge and panels."""

from __future__ import annotations

import html
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lantai.mcp_app.protocol import MCP_APP_RESOURCE_URI, read_app_resource
from tests.support.ui_browser import chromium


@dataclass(frozen=True)
class _AppContext:
    """沙箱 iframe 里的执行上下文：所在 CDP 会话 + 隔离世界的 contextId。"""

    session: str
    context_id: int


async def _send_in(page, session: str, method: str, params: dict | None = None) -> dict:
    """在指定 CDP 会话里发一条命令，发完换回页面自己的会话。"""
    previous = page.session
    page.session = session
    try:
        return await page.send(method, params)
    finally:
        page.session = previous


async def _app_context(page) -> _AppContext:
    """找到 MCP App 的沙箱 iframe，在里面建一个隔离世界。

    headless shell 把 sandbox="allow-scripts" 的 srcdoc iframe 留在页面进程里，
    它会出现在页面的 frame tree 下。完整版 Chrome（CI 用的 /usr/bin/google-chrome）
    默认开着 IsolateSandboxedIframes，沙箱 iframe 被放进独立进程（OOPIF），
    页面会话的 frame tree 里没有 childFrames，只能作为 iframe target 附着上去。
    两种都要支持，否则测试只在其中一种浏览器上能跑。
    """
    frames = await page.send("Page.getFrameTree")
    children = frames["frameTree"].get("childFrames") or []
    if children:
        session = page.session
        frame_id = children[0]["frame"]["id"]
    else:
        targets = await page.send("Target.getTargets", browser=True)
        iframes = [
            info
            for info in targets["targetInfos"]
            if info["type"] == "iframe" and info["url"] == "about:srcdoc"
        ]
        assert len(iframes) == 1, targets["targetInfos"]
        attached = await page.send(
            "Target.attachToTarget",
            {"targetId": iframes[0]["targetId"], "flatten": True},
            browser=True,
        )
        session = attached["sessionId"]
        # OOPIF 的 target id 就是它的 frame id。
        frame_id = iframes[0]["targetId"]
    world = await _send_in(
        page,
        session,
        "Page.createIsolatedWorld",
        {"frameId": frame_id, "worldName": "lantai-mcp-app-test"},
    )
    return _AppContext(session=session, context_id=world["executionContextId"])


async def _app_eval(page, context: _AppContext, expression: str):
    result = await _send_in(
        page,
        context.session,
        "Runtime.evaluate",
        {
            "expression": expression,
            "contextId": context.context_id,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        },
    )
    assert "exceptionDetails" not in result, result
    return result["result"].get("value")


@contextmanager
def _host_page(*, tool_name: str, server_tools: bool = True) -> Iterator[str]:
    resource = read_app_resource(MCP_APP_RESOURCE_URI)["text"]
    scope = {
        "user_id": "user-a",
        "tenant_id": "tenant-a",
        "agent_id": "agent-a",
        "federation_id": "federation-a",
    }
    payloads = {
        "core_memory_get": {
            "blocks": [
                {
                    "block": "identity",
                    "content": '<img src=x onerror="window.__xss=true">核心身份',
                }
            ]
        },
        "mem_recent": {"memories": [{"id": "recent-1", "content": "最近记忆"}]},
        "search": {"results": [{"id": "search-1", "content": "语义检索命中"}]},
        "crystals_list": {
            "crystals": [
                {"id": "crystal-a", "skill_name": "候选甲", "procedure": "原始步骤"},
                {"id": "crystal-b", "skill_name": "候选乙", "procedure": "备用步骤"},
            ]
        },
        "crystal_decide": {"ok": True},
    }
    document = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>MCP App host test</title>
<iframe id="app-frame" sandbox="allow-scripts" onload="window.__iframeLoaded=true" srcdoc="{html.escape(resource, quote=True)}"></iframe>
<script>
window.__calls = [];
const toolName = {json.dumps(tool_name)};
const scope = {json.dumps(scope, ensure_ascii=False)};
const payloads = {json.dumps(payloads, ensure_ascii=False)};
const serverTools = {json.dumps({} if server_tools else None)};
function reply(source, id, result) {{ source.postMessage({{jsonrpc:'2.0', id, result}}, '*'); }}
function hostContext(theme='dark') {{ return {{
  theme,
  styles: {{variables: {{'--color-background-primary': theme === 'light' ? '#f8fafb' : '#17212b'}}}},
  toolInfo: {{tool: {{name: toolName}}, toolCallId: 'host-call-1'}}
}}; }}
window.addEventListener('message', event => {{
  const message = event.data;
  if (!message || message.jsonrpc !== '2.0') return;
  if (message.method === 'ui/initialize') {{
    reply(event.source, message.id, {{protocolVersion:'2026-01-26', hostCapabilities:serverTools ? {{serverTools}} : {{}}, hostContext:hostContext()}});
  }} else if (message.method === 'ui/notifications/initialized') {{
    event.source.postMessage({{jsonrpc:'2.0', method:'ui/notifications/tool-input', params:{{arguments:scope}}}}, '*');
  }} else if (message.method === 'tools/call') {{
    window.__calls.push(message.params);
    const value = payloads[message.params.name] || {{}};
    reply(event.source, message.id, {{content:[{{type:'text', text:JSON.stringify(value)}}]}});
  }}
}});
window.changeHostTheme = () => document.querySelector('#app-frame').contentWindow.postMessage({{
  jsonrpc:'2.0', method:'ui/notifications/host-context-changed',
  params:{{hostContext:hostContext('light')}}
}}, '*');
</script></html>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            body = document.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.asyncio
async def test_app_uses_host_bridge_follows_theme_and_renders_untrusted_text(
    tmp_path: Path,
) -> None:
    with _host_page(tool_name="core_memory_get") as url:
        async with chromium(
            tmp_path,
            url,
            ready="document.querySelector('#app-frame')",
        ) as page:
            await page.wait("window.__iframeLoaded === true")
            context_id = await _app_context(page)
            await _app_eval(
                page,
                context_id,
                "new Promise(resolve => { const check = () => document.querySelector('#coreBlocks .record-card') && document.querySelector('#recentMemories .record-card') ? resolve(true) : requestAnimationFrame(check); check(); })",
            )
            assert await _app_eval(
                page, context_id, "document.querySelector('#overviewPanel').textContent"
            )
            assert await _app_eval(
                page,
                context_id,
                "document.querySelector('#overviewPanel img') === null && document.querySelector('#overviewPanel').textContent.includes('<img src=x onerror=\"window.__xss=true\">')",
            )
            assert await _app_eval(page, context_id, "window.__xss === undefined")
            calls = await page.evaluate("window.__calls")
            assert {call["name"] for call in calls} >= {"core_memory_get", "mem_recent"}
            assert all(call["arguments"]["user_id"] == "user-a" for call in calls)
            assert await _app_eval(
                page, context_id, "document.querySelector('[data-tab=crystals]').hidden"
            )

            await page.evaluate("window.changeHostTheme()")
            await page.wait("document.querySelector('#app-frame').contentWindow !== null")
            await _app_eval(
                page,
                context_id,
                "new Promise(resolve => { const check = () => document.documentElement.dataset.theme === 'light' ? resolve(true) : requestAnimationFrame(check); check(); })",
            )
            assert await _app_eval(
                page,
                context_id,
                "getComputedStyle(document.body).backgroundColor === 'rgb(248, 250, 251)'",
            )

            await _app_eval(page, context_id, "document.querySelector('[data-tab=search]').click()")
            assert await _app_eval(
                page, context_id, "document.querySelector('#searchPanel').hidden === false"
            )
            await _app_eval(
                page,
                context_id,
                "document.querySelector('#searchQuery').value = '会议记忆'; document.querySelector('#searchSubmit').click()",
            )
            calls = await page.evaluate("window.__calls")
            search_state = await _app_eval(
                page,
                context_id,
                "new Promise(resolve => setTimeout(() => resolve({status:document.querySelector('#status').textContent,disabled:document.querySelector('#searchSubmit').disabled,query:document.querySelector('#searchQuery').value,valid:document.querySelector('#searchForm').checkValidity()}), 250))",
            )
            assert any(call["name"] == "search" for call in calls), (search_state, calls)
            search_call = next(call for call in calls if call["name"] == "search")
            assert search_call["arguments"]["query"] == "会议记忆"
            assert search_call["arguments"]["tenant_id"] == "tenant-a"
            assert await _app_eval(
                page,
                context_id,
                "document.querySelector('#searchPanel').textContent.includes('语义检索命中')",
            )
            await _app_eval(
                page,
                context_id,
                "document.querySelector('#searchQuery').value = '键盘检索'; document.querySelector('#searchQuery').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',bubbles:true}))",
            )
            await page.wait("window.__calls.filter(call => call.name === 'search').length === 2")
            await _app_eval(
                page,
                context_id,
                "document.querySelector('#searchQuery').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',isComposing:true,bubbles:true}))",
            )
            assert (
                len(
                    [
                        call
                        for call in await page.evaluate("window.__calls")
                        if call["name"] == "search"
                    ]
                )
                == 2
            )


@pytest.mark.asyncio
async def test_crystal_decisions_require_confirmation_input_and_keep_scope(
    tmp_path: Path,
) -> None:
    with _host_page(tool_name="crystals_list") as url:
        async with chromium(
            tmp_path,
            url,
            ready="document.querySelector('#app-frame')",
        ) as page:
            await page.wait("window.__iframeLoaded === true")
            context_id = await _app_context(page)
            await _app_eval(
                page,
                context_id,
                "new Promise(resolve => { const check = () => document.querySelector('[data-decision=approve]') ? resolve(true) : requestAnimationFrame(check); check(); })",
            )
            assert await _app_eval(
                page,
                context_id,
                "document.querySelector('#crystalsPanel').textContent.includes('候选甲')",
            )

            await _app_eval(
                page,
                context_id,
                "document.querySelector('[data-decision=approve][data-id=crystal-a]').click()",
            )
            assert await _app_eval(
                page, context_id, "document.querySelector('#decisionDialog').open"
            )
            await _app_eval(
                page, context_id, "document.querySelector('[data-cancel-decision]').click()"
            )
            calls = await page.evaluate("window.__calls")
            assert not [call for call in calls if call["name"] == "crystal_decide"]

            await _app_eval(
                page,
                context_id,
                "document.querySelector('[data-decision=approve][data-id=crystal-a]').click()",
            )
            await _app_eval(
                page,
                context_id,
                "document.querySelector('#decisionSteps').value = '核对输入\\n复核结论'; document.querySelector('#decisionConfirm').click()",
            )
            calls = await page.evaluate("window.__calls")
            decision_state = await _app_eval(
                page,
                context_id,
                "new Promise(resolve => setTimeout(() => resolve({status:document.querySelector('#status').textContent,open:document.querySelector('#decisionDialog').open,steps:document.querySelector('#decisionSteps').value,valid:document.querySelector('#decisionSteps').validationMessage}), 250))",
            )
            assert any(call["name"] == "crystal_decide" for call in calls), (
                decision_state,
                calls,
            )
            approval = next(call["arguments"] for call in calls if call["name"] == "crystal_decide")
            assert approval == {
                "crystal_id": "crystal-a",
                "approve": True,
                "steps": ["核对输入", "复核结论"],
                "reason": "",
                "user_id": "user-a",
                "tenant_id": "tenant-a",
                "agent_id": "agent-a",
                "federation_id": "federation-a",
            }

            await _app_eval(
                page,
                context_id,
                "document.querySelector('[data-decision=reject][data-id=crystal-b]').click()",
            )
            await _app_eval(page, context_id, "document.querySelector('#decisionConfirm').click()")
            calls = await page.evaluate("window.__calls")
            assert len([call for call in calls if call["name"] == "crystal_decide"]) == 1
            await _app_eval(
                page,
                context_id,
                "document.querySelector('#decisionReason').value = '不符合团队流程'; document.querySelector('#decisionConfirm').click()",
            )
            await page.wait(
                "window.__calls.filter(call => call.name === 'crystal_decide').length === 2"
            )
            calls = await page.evaluate("window.__calls")
            rejection = [call["arguments"] for call in calls if call["name"] == "crystal_decide"][1]
            assert rejection["crystal_id"] == "crystal-b"
            assert rejection["approve"] is False
            assert rejection["reason"] == "不符合团队流程"
            assert rejection["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_app_without_server_tool_capability_stays_read_only(tmp_path: Path) -> None:
    with _host_page(tool_name="core_memory_get", server_tools=False) as url:
        async with chromium(
            tmp_path,
            url,
            ready="document.querySelector('#app-frame')",
        ) as page:
            await page.wait("window.__iframeLoaded === true")
            context_id = await _app_context(page)
            await _app_eval(
                page,
                context_id,
                "new Promise(resolve => { const check = () => document.querySelector('#status').textContent.includes('未开放') ? resolve(true) : requestAnimationFrame(check); check(); })",
            )
            assert await page.evaluate("window.__calls") == []
            assert await _app_eval(
                page,
                context_id,
                "document.querySelector('#status').textContent.includes('未开放')",
            )
