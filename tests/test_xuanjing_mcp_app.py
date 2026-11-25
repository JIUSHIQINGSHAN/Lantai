"""MCP App resource and browser behavior for the Xuanjing observatory."""

from __future__ import annotations

import pytest


def test_xuanjing_resource_contract_is_read_only_and_host_registered():
    from lantai.mcp_apps.xuanjing import (
        RESOURCE_MIME_TYPE,
        RESOURCE_URI,
        TOOL_UI_METADATA,
        list_xuanjing_app_resources,
        read_xuanjing_app_resource,
    )

    resources = list_xuanjing_app_resources()
    assert len(resources) == 1
    assert resources[0]["uri"] == "ui://lantai/xuanjing-observatory"
    assert resources[0]["mimeType"] == "text/html;profile=mcp-app"
    assert resources[0]["uri"] == RESOURCE_URI
    assert resources[0]["mimeType"] == RESOURCE_MIME_TYPE
    assert TOOL_UI_METADATA == {
        "ui": {
            "resourceUri": RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }

    result = read_xuanjing_app_resource(RESOURCE_URI)
    assert result["contents"][0]["uri"] == RESOURCE_URI
    assert result["contents"][0]["mimeType"] == RESOURCE_MIME_TYPE
    assert "ui/initialize" in result["contents"][0]["text"]


def test_xuanjing_app_resource_has_no_external_or_write_capability():
    from lantai.mcp_apps.xuanjing import RESOURCE_METADATA, read_xuanjing_app_resource

    html = read_xuanjing_app_resource("ui://lantai/xuanjing-observatory")["contents"][0]["text"]
    assert RESOURCE_METADATA["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": [],
        "baseUriDomains": [],
    }
    assert "get_xuanjing_graph" in html
    assert "fetch(" not in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "clipboard" not in html.lower()


def test_browser_renders_graph_and_links_timeline_selection():
    playwright_api = pytest.importorskip("playwright.sync_api")
    from lantai.mcp_apps.xuanjing import RESOURCE_URI, read_xuanjing_app_resource

    payload = {
        "generated_at": "2026-11-25T10:00:00Z",
        "watermark": "2026-11-25T10:00:00Z",
        "stream_cursor": "snapshot-cursor",
        "nodes": [
            {
                "id": "mem-alpha",
                "label": "首条记忆",
                "content": "用于验证时间轴选择",
                "lane": "fact",
                "domain": "project",
                "decay_score": 0.8,
                "importance": 0.7,
                "weight": 1.5,
                "event_time": "2026-11-20T08:00:00Z",
                "valid_from": "2026-11-20T08:00:00Z",
                "valid_to": None,
                "created_at": "2026-11-20T08:00:00Z",
                "updated_at": "2026-11-24T09:00:00Z",
                "tags": ["alpha"],
                "lifecycle_status": "active",
                "coordinates": {"space": {}, "time": {}},
            },
            {
                "id": "mem-beta",
                "label": "第二条记忆",
                "content": "另一条拓扑记录",
                "lane": "preference",
                "domain": "profile",
                "decay_score": 0.6,
                "importance": 0.5,
                "weight": 1.0,
                "event_time": "2026-11-22T08:00:00Z",
                "valid_from": "2026-11-22T08:00:00Z",
                "valid_to": None,
                "created_at": "2026-11-22T08:00:00Z",
                "updated_at": "2026-11-23T09:00:00Z",
                "tags": [],
                "lifecycle_status": "active",
                "coordinates": {"space": {}, "time": {}},
            },
        ],
        "edges": [
            {
                "id": "edge-alpha-beta",
                "source": "mem-alpha",
                "target": "mem-beta",
                "relation": "supports",
                "confidence": 0.9,
                "provenance": {},
            }
        ],
        "clusters": [],
        "scenes": [],
        "stats": {
            "node_count": 2,
            "edge_count": 1,
            "total_nodes": 2,
            "total_edges": 1,
            "avg_decay_score": 0.7,
            "lane_counts": {"fact": 1, "preference": 1},
            "relation_counts": {"supports": 1},
        },
        "has_more": True,
        "next_cursor": "cursor-page-2",
        "pagination": {},
    }
    next_page = {
        "generated_at": "2026-11-25T10:00:00Z",
        "watermark": "2026-11-25T10:00:00Z",
        "stream_cursor": "snapshot-cursor",
        "nodes": [
            {
                "id": "mem-gamma",
                "label": "第三条记忆",
                "content": "来自下一页",
                "lane": "rule",
                "domain": "project",
                "decay_score": 0.4,
                "importance": 0.3,
                "weight": 0.8,
                "event_time": "2026-11-21T08:00:00Z",
                "valid_from": "2026-11-21T08:00:00Z",
                "valid_to": None,
                "created_at": "2026-11-21T08:00:00Z",
                "updated_at": "2026-11-22T09:00:00Z",
                "tags": [],
                "lifecycle_status": "active",
                "coordinates": {"space": {}, "time": {}},
            },
            {
                "id": "mem-delta",
                "label": "第四条记忆",
                "content": "同页关系的另一端",
                "lane": "experience",
                "domain": "project",
                "decay_score": 0.6,
                "importance": 0.4,
                "weight": 0.9,
                "event_time": "2026-11-19T08:00:00Z",
                "valid_from": "2026-11-19T08:00:00Z",
                "valid_to": None,
                "created_at": "2026-11-20T08:00:00Z",
                "updated_at": "2026-11-21T09:00:00Z",
                "tags": [],
                "lifecycle_status": "active",
                "coordinates": {"space": {}, "time": {}},
            },
        ],
        "edges": [
            {
                "id": "edge-gamma-delta",
                "source": "mem-gamma",
                "target": "mem-delta",
                "relation": "refines",
                "confidence": 0.8,
                "provenance": {},
            }
        ],
        "clusters": [],
        "scenes": [],
        "stats": {
            "node_count": 2,
            "edge_count": 1,
            "total_nodes": 2,
            "total_edges": 1,
            "avg_decay_score": 0.5,
            "lane_counts": {"rule": 1, "experience": 1},
            "relation_counts": {"refines": 1},
        },
        "has_more": False,
        "next_cursor": None,
        "pagination": {},
    }
    resource = read_xuanjing_app_resource(RESOURCE_URI)["contents"][0]

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            try:
                browser = playwright.chromium.launch(headless=True, channel="msedge")
            except Exception as exc:
                pytest.skip(f"No installed Playwright browser is available: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_content(
            "<style>html,body{width:100%;height:100%;margin:0}iframe{display:block;width:100%;height:100%;border:0}</style>"
            '<iframe title="悬镜 MCP App"></iframe>'
        )
        page.evaluate(
            """({ html, graph, nextPage }) => {
                window.__toolCalls = [];
                window.__initialized = false;
                window.__callsBeforeInitialized = [];
                window.__pendingInitialize = null;
                window.__holdInitialize = true;
                window.addEventListener("message", (event) => {
                    const message = event.data;
                    if (!message || message.jsonrpc !== "2.0") return;
                    if (message.method === "ui/initialize") {
                        if (window.__holdInitialize) {
                            window.__pendingInitialize = { source: event.source, id: message.id };
                        } else {
                            event.source.postMessage({ jsonrpc: "2.0", id: message.id, result: {
                                protocolVersion: "2026-01-26",
                                hostInfo: { name: "test-host", version: "1" },
                                hostCapabilities: { serverTools: {} },
                                hostContext: { theme: "light" }
                            } }, "*");
                        }
                    } else if (message.method === "ui/notifications/initialized") {
                        window.__initialized = true;
                        event.source.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-input",
                            params: { arguments: { limit: 250 } }
                        }, "*");
                        event.source.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result",
                            params: { content: [{ type: "text", text: JSON.stringify(graph) }] }
                        }, "*");
                    } else if (message.method === "tools/call") {
                        if (!window.__initialized) window.__callsBeforeInitialized.push(message.method);
                        window.__toolCalls.push(message.params);
                        if (window.__nextToolError) {
                            window.__nextToolError = false;
                            event.source.postMessage({ jsonrpc: "2.0", id: message.id,
                                error: { code: -32603, message: "只读图谱暂不可用" }
                            }, "*");
                        } else {
                            const payload = message.params.arguments.cursor
                                ? window.__nextPagePayload
                                : window.__graphPayload;
                            event.source.postMessage({ jsonrpc: "2.0", id: message.id,
                                result: window.__structuredToolResult
                                    ? { structuredContent: payload }
                                    : { content: [{ type: "text", text: JSON.stringify(payload) }] }
                            }, "*");
                        }
                    }
                });
                window.__graphPayload = graph;
                window.__nextPagePayload = nextPage;
                window.__structuredToolResult = false;
                window.__nextToolError = false;
                document.querySelector("iframe").srcdoc = html;
            }""",
            {"html": resource["text"], "graph": payload, "nextPage": next_page},
        )
        frame = page.frame_locator("iframe")
        page.wait_for_function("window.__pendingInitialize !== null")
        frame.locator("#refresh-button").click()
        assert page.evaluate("window.__toolCalls") == []
        page.evaluate(
            """() => {
                const pending = window.__pendingInitialize;
                pending.source.postMessage({ jsonrpc: "2.0", id: pending.id, result: {
                    protocolVersion: "2026-01-26",
                    hostInfo: { name: "test-host", version: "1" },
                    hostCapabilities: { serverTools: {} },
                    hostContext: { theme: "light" }
                } }, "*");
            }"""
        )
        frame.locator('#node-list [data-node-id="mem-alpha"]').wait_for(timeout=5000)
        assert frame.locator("#node-count").inner_text() == "2"
        assert frame.locator("#edge-count").inner_text() == "1"
        assert frame.locator("#decay-average").inner_text() == "70%"
        assert frame.locator("#watermark").inner_text() != "—"
        assert frame.locator("#graph-svg .node-mark").count() == 2
        assert frame.locator("#graph-svg line.edge").count() == 1

        load_more = frame.locator("#load-more-button")
        load_more.wait_for(state="visible", timeout=5000)
        page.evaluate("window.__structuredToolResult = true")
        load_more.click()
        page.wait_for_function("window.__toolCalls.length === 1")
        assert page.evaluate("window.__toolCalls") == [
            {
                "name": "get_xuanjing_graph",
                "arguments": {"limit": 250, "cursor": "cursor-page-2"},
            }
        ]
        assert frame.locator("#node-count").inner_text() == "4"
        frame.locator('#node-list [data-node-id="mem-gamma"]').wait_for(timeout=5000)
        assert frame.locator('#node-list [data-node-id="mem-delta"]').count() == 1
        assert frame.locator("#node-list [data-node-id]").count() == 4
        assert frame.locator("#graph-svg .node-mark").count() == 4
        assert frame.locator("#graph-svg line.edge").count() == 2
        assert frame.locator("#decay-average").inner_text() == "60%"
        assert load_more.is_hidden()

        frame.locator('#node-list [data-node-id="mem-gamma"]').click()
        edge_target = frame.locator('#selected-edges [data-edge-target="mem-delta"]')
        assert edge_target.count() == 1
        edge_target.click()
        assert frame.locator("#selected-node-title").inner_text() == "第四条记忆"

        frame.locator('[data-timeline-node="mem-beta"]').click()
        assert frame.locator("#selected-node-title").inner_text() == "第二条记忆"
        assert (
            frame.locator('#node-list [data-node-id="mem-beta"]').get_attribute("aria-pressed")
            == "true"
        )
        frame.locator("#time-cutoff").evaluate(
            "slider => { slider.value = String(Date.parse('2026-11-20T08:00:00Z')); "
            "slider.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        assert frame.locator("#node-list [data-node-id]").count() == 2
        assert frame.locator("#graph-svg .node-mark").count() == 2
        frame.locator('[data-timeline-node="mem-beta"]').click()
        assert frame.locator("#node-list [data-node-id]").count() == 4

        frame.locator("#lane-filter").select_option("fact")
        page.wait_for_function("window.__toolCalls.length === 2")
        calls = page.evaluate("window.__toolCalls")
        assert calls[-1:] == [
            {"name": "get_xuanjing_graph", "arguments": {"limit": 250, "lane": "fact"}}
        ]
        assert page.evaluate("window.__callsBeforeInitialized") == []
        frame.locator("#domain-filter").fill("project")
        frame.locator("#theme-filter").focus()
        page.wait_for_function("window.__toolCalls.length === 3")
        calls = page.evaluate("window.__toolCalls")
        assert calls[-1:] == [
            {
                "name": "get_xuanjing_graph",
                "arguments": {"limit": 250, "lane": "fact", "domain": "project"},
            }
        ]
        frame.locator("#theme-filter").fill("memory")
        frame.locator("#refresh-button").click()
        page.wait_for_function("window.__toolCalls.length === 4")
        calls = page.evaluate("window.__toolCalls")
        assert calls[-1:] == [
            {
                "name": "get_xuanjing_graph",
                "arguments": {
                    "limit": 250,
                    "lane": "fact",
                    "domain": "project",
                    "theme": "memory",
                },
            }
        ]

        page.evaluate("window.__nextPagePayload = { nodes: 'malformed', edges: [] }")
        load_more.wait_for(state="visible", timeout=5000)
        load_more.click()
        page.wait_for_function("window.__toolCalls.length === 5")
        frame.locator('#app-status[data-state="error"]').wait_for(timeout=5000)
        assert "不符合悬镜图谱结构" in frame.locator("#app-status").inner_text()
        assert frame.locator("#node-count").inner_text() == "2"
        assert frame.locator("#edge-count").inner_text() == "1"
        assert frame.locator("#node-list [data-node-id]").count() == 2

        frame.locator("body").evaluate("element => element.scrollWidth <= element.clientWidth")
        for width in (390, 320, 300):
            page.set_viewport_size({"width": width, "height": 844})
            overflow = frame.locator("html").evaluate(
                "element => ({width: element.clientWidth, scrollWidth: element.scrollWidth, "
                "offenders: [...element.querySelectorAll('body *')].filter(child => "
                "child.getBoundingClientRect().right > element.clientWidth + 1).map(child => "
                "({tag: child.tagName, id: child.id, className: child.className, "
                "right: Math.round(child.getBoundingClientRect().right)}))})"
            )
            assert overflow["scrollWidth"] <= overflow["width"], overflow

        page.evaluate("window.__nextToolError = true")
        frame.locator("#refresh-button").click()
        frame.locator('#app-status[data-state="error"]').wait_for(timeout=5000)
        assert "只读图谱暂不可用" in frame.locator("#app-status").inner_text()

        page.evaluate(
            """() => {
                const app = document.querySelector("iframe").contentWindow;
                const empty = { nodes: [], edges: [], stats: { total_nodes: 0, total_edges: 0 } };
                app.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result",
                    params: { content: [{ type: "text", text: JSON.stringify(empty) }] }
                }, "*");
            }"""
        )
        frame.locator("#node-list-empty").filter(has_text="暂无记忆节点").wait_for(timeout=5000)
        assert frame.locator("#node-count").inner_text() == "0"
        browser.close()
