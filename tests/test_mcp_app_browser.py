"""Real Chromium coverage for the MCP App sandbox message bridge and panels."""

from __future__ import annotations

import html
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect

from lantai.mcp_app.protocol import MCP_APP_RESOURCE_URI, read_app_resource
from tests.support.browser_e2e import browser_page
from tests.support.ui_browser import chromium
from tests.test_xuanjing_ui_smoke import UNTRUSTED
from tests.test_xuanjing_ui_smoke import observatory as observatory
from tests.test_xuanjing_ui_smoke import ui_smoke_env as ui_smoke_env


async def _app_context(page) -> int:
    frames = await page.send("Page.getFrameTree")
    app_frame = frames["frameTree"]["childFrames"][0]["frame"]["id"]
    world = await page.send(
        "Page.createIsolatedWorld",
        {"frameId": app_frame, "worldName": "lantai-mcp-app-test"},
    )
    return world["executionContextId"]


async def _app_eval(page, context_id: int, expression: str):
    result = await page.send(
        "Runtime.evaluate",
        {
            "expression": expression,
            "contextId": context_id,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        },
    )
    assert "exceptionDetails" not in result, result
    return result["result"].get("value")


@contextmanager
def _host_page(
    *, tool_name: str, server_tools: bool = True, tool_delay_ms: int = 0, failed_tool: str = ""
) -> Iterator[str]:
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
const toolDelayMs = {tool_delay_ms};
const failedTool = {json.dumps(failed_tool)};
window.__inFlight = 0;
window.__maxInFlight = 0;
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
    window.__inFlight += 1;
    window.__maxInFlight = Math.max(window.__maxInFlight, window.__inFlight);
    window.setTimeout(() => {{
      if (message.params.name === failedTool) {{
        event.source.postMessage({{jsonrpc:'2.0', id:message.id, error:{{code:-32603, message:'synthetic error'}}}}, '*');
      }} else {{
        reply(event.source, message.id, {{content:[{{type:'text', text:JSON.stringify(value)}}]}});
      }}
      window.__inFlight -= 1;
    }}, toolDelayMs);
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
async def test_app_loads_overview_tools_in_parallel(tmp_path: Path) -> None:
    with _host_page(tool_name="core_memory_get", tool_delay_ms=60) as url:
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
            assert await page.evaluate("window.__maxInFlight") >= 2
            assert {call["name"] for call in await page.evaluate("window.__calls")} >= {
                "core_memory_get",
                "mem_recent",
            }


def test_app_overview_failure_keeps_the_other_panel_available(tmp_path: Path) -> None:
    with _host_page(tool_name="core_memory_get", failed_tool="core_memory_get") as url:
        with browser_page(tmp_path, url) as page:
            frame = page.frame_locator("iframe")
            expect(frame.locator("#status")).to_have_text("宿主工具调用失败")
            expect(frame.locator("#recentMemories .record-card p")).to_have_text("最近记忆")
            frame.locator("#tab-overview").focus()
            frame.locator("#tab-overview").press("ArrowRight")
            expect(frame.locator("#searchQuery")).to_be_visible()
            assert [call["name"] for call in page.evaluate("window.__calls")] == [
                "core_memory_get",
                "mem_recent",
            ]


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


def test_live_panel_hydrates_navigates_and_renders_nested_search(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/__test_host__?kind=core") as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#status")).to_have_text("记忆总览已更新")
        expect(frame.locator("#coreBlocks .record-card p")).to_have_text(UNTRUSTED)
        expect(frame.locator("#recentMemories .record-card")).to_have_count(2)
        assert frame.locator("#overviewPanel img").count() == 0
        frame.locator("#tab-overview").focus()
        frame.locator("#tab-overview").press("ArrowRight")
        expect(frame.locator("#tab-search")).to_be_focused()
        expect(frame.locator("#searchPanel")).to_be_visible()
        # The real search wire contract nests each memory in results[].memory.
        page.evaluate("""window.__hostResult = {structuredContent:{results:[{
            memory:{id:'browser-alpha',title:'搜索正文',content:'<img src=x onerror=window.__xss=true>命中正文'},
            score:0.95}]}}""")
        frame.locator("#searchQuery").fill("不可执行正文")
        frame.locator("#searchQuery").press("Enter")
        expect(frame.locator("#status")).to_have_text("检索完成")
        expect(frame.locator("#searchResults h3")).to_have_text("搜索正文")
        expect(frame.locator("#searchResults p")).to_have_text(
            "<img src=x onerror=window.__xss=true>命中正文"
        )
        assert frame.locator("#searchResults img").count() == 0
        assert frame.locator("body").evaluate("() => window.__xss === undefined")
        page.evaluate("window.__hostResult = {structuredContent:{results:[]}}")
        frame.locator("#searchSubmit").click()
        expect(frame.locator("#searchResults")).to_have_text("没有检索结果")
        frame.locator("#tab-search").focus()
        frame.locator("#tab-search").press("Home")
        expect(frame.locator("#overviewPanel")).to_be_visible()
        assert [call["name"] for call in page.evaluate("window.__calls")] == [
            "core_memory_get",
            "mem_recent",
            "search",
            "search",
        ]


@pytest.mark.parametrize("nested", [True, False])
def test_live_panel_theme_switch_without_style_variables(observatory, tmp_path, nested):
    with browser_page(tmp_path, observatory + "/__test_host__?kind=core", width=390) as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#status")).to_have_text("记忆总览已更新")
        page.evaluate("nested => window.sendContext({theme:'dark'}, nested)", nested)
        expect(frame.locator("html")).to_have_attribute("data-theme", "dark")
        expect(frame.locator("body")).to_have_css("background-color", "rgb(23, 33, 43)")
        page.evaluate("nested => window.sendContext({theme:'light'}, nested)", nested)
        expect(frame.locator("html")).to_have_attribute("data-theme", "light")
        expect(frame.locator("body")).to_have_css("background-color", "rgb(248, 250, 251)")
        expect(frame.locator("body")).to_have_css("color", "rgb(23, 33, 43)")
        assert frame.locator("html").evaluate("e => e.scrollWidth <= e.clientWidth")


@pytest.mark.parametrize("refresh", ["overview", "recent"])
def test_live_panel_refresh_failure_is_visible_and_recoverable(observatory, tmp_path, refresh):
    with browser_page(tmp_path, observatory + "/__test_host__?kind=core") as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#status")).to_have_text("记忆总览已更新")
        page.evaluate("window.__hostError = '刷新失败'")
        frame.locator(f'[data-refresh="{refresh}"]').click()
        expect(frame.locator("#status")).to_have_text("宿主工具调用失败")
        expect(frame.locator("#coreBlocks .record-card")).to_have_count(1)
        frame.locator(f'[data-refresh="{refresh}"]').click()
        expect(frame.locator("#status")).to_have_text("记忆已更新")


def test_live_read_only_panel_accepts_initial_result(observatory, tmp_path):
    with browser_page(
        tmp_path, observatory + "/__test_host__?kind=core&server_tools=false"
    ) as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#status")).to_contain_text("未开放工具调用")
        page.evaluate("window.sendResult({structuredContent:{blocks:[]}})")
        expect(frame.locator("#coreBlocks")).to_have_text("暂无核心记忆")
        expect(frame.locator('[data-refresh="overview"]')).to_be_disabled()
        expect(frame.locator("#searchSubmit")).to_be_disabled()
        assert page.evaluate("window.__calls") == []
