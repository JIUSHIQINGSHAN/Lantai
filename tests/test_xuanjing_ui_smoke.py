"""悬镜·UI 控制台星图观测台面板集成冒烟测试 (Ticket 02)

验证接缝（Seams）：
1. GET /ui 返回的控制台页面中包含「悬镜星图」专属主导航项与工作区视图；
2. GET /ui/assets/xuanjing.js 静态脚本能够正常托管分发，且 Content-Type 为 javascript；
3. GET /ui/map 与 GET /graph/xuanjing 数据流通畅，UI 可观测性闭环。
"""

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.core.settings import settings
from lantai.models.tables import CoreMemoryBlock, MemoryEdge, MemoryItem
from tests.support.browser_e2e import browser_page, install_mcp_host, live_server

UNTRUSTED = '<img src=x onerror="window.__xss=true">不可执行正文'


@pytest.fixture()
def ui_smoke_env(monkeypatch, tmp_path):
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'browser.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    yield session_factory
    engine.dispose()


def test_ui_index_contains_xuanjing_observatory(ui_smoke_env):
    """Seam 1: 验证控制台页面包含「悬镜星图」导航与专属观测台视图容器。"""
    client = TestClient(app)
    resp = client.get("/ui")
    assert resp.status_code == 200
    html = resp.text

    # 验证主侧边栏存在悬镜导航项
    assert 'data-view="xuanjing"' in html
    assert "悬镜星图" in html or "悬镜观测台" in html

    # 验证主体工作区存在 xuanjingView 容器
    assert 'id="xuanjingView"' in html
    assert 'id="xuanjingCanvas"' in html or 'id="xuanjingSvg"' in html


def test_ui_xuanjing_assets_served():
    """Seam 2: 验证 xuanjing.js 静态文件托管。"""
    client = TestClient(app)
    resp = client.get("/ui/assets/xuanjing.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")
    assert "initXuanjing" in resp.text or "renderXuanjing" in resp.text


@pytest.fixture
def observatory(ui_smoke_env, monkeypatch):
    from lantai.api.app import create_app

    monkeypatch.setattr(settings, "API_KEY", "browser-synthetic-key")
    monkeypatch.setenv("LANTAI_MCP_PROFILE", "full")
    with ui_smoke_env() as session:
        session.add_all(
            [
                CoreMemoryBlock(id="browser-core", block="identity", content=UNTRUSTED),
                MemoryItem(id="browser-alpha", key="Alpha", content=UNTRUSTED, lane="fact"),
                MemoryItem(id="browser-beta", key="Beta", content="另一条偏好", lane="preference"),
            ]
        )
        session.add(
            MemoryEdge(
                id="browser-edge",
                source_memory_id="browser-alpha",
                target_memory_id="browser-beta",
                relation="supports",
                confidence=0.9,
            )
        )
        session.commit()
    application = create_app()
    install_mcp_host(application)
    with live_server(application) as url:
        yield url


def _connect(page):
    page.evaluate("sessionStorage.setItem('lantai-api-key-session', 'browser-synthetic-key')")
    page.reload()
    expect(page.locator("#xuanjingNodeCount")).to_have_text("2")
    expect(page.locator(".xuanjing-node-item")).to_have_count(2)


def test_observatory_real_api_navigation_search_detail_and_theme(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/ui#/xuanjing") as page:
        _connect(page)
        expect(page.locator("#xuanjingEdgeCount")).to_have_text("1")
        assert page.locator("#xuanjingSvg .xuanjing-links line").count() == 1
        assert page.evaluate("typeof window.d3.forceSimulation") == "function"
        page.locator("#xuanjingSearchInput").fill("不可执行")
        beta = page.locator(".xuanjing-node-item").filter(has_text="另一条偏好")
        expect(beta).to_have_css("opacity", "0.15")
        alpha = page.locator(".xuanjing-node-item").filter(has_text="<img")
        alpha.dispatch_event("click")
        expect(page.locator("#xuanjingDrawer")).to_be_visible()
        expect(page.locator("#xjdContent")).to_have_text(UNTRUSTED)
        assert page.locator("#xjdContent img, #xuanjingSvg image").count() == 0
        assert page.evaluate("window.__xss === undefined")
        page.locator("#xuanjingCloseDrawer").click()
        expect(page.locator("#xuanjingDrawer")).to_be_hidden()
        before = page.locator("#xuanjingSvgContainer").evaluate(
            "e => getComputedStyle(e).backgroundImage"
        )
        page.locator("#themeButton").click()
        after = page.locator("#xuanjingSvgContainer").evaluate(
            "e => getComputedStyle(e).backgroundImage"
        )
        assert before != after
        chosen = page.locator("html").get_attribute("data-theme")
        page.locator('.sidebar [data-view="tasks"]').click()
        expect(page.locator("#tasksView")).to_have_class("view active")
        page.go_back()
        expect(page.locator("#xuanjingView")).to_have_class("view active")
        expect(page.locator("html")).to_have_attribute("data-theme", chosen)
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", chosen)
        expect(page.locator(".xuanjing-node-item")).to_have_count(2)


def test_observatory_filter_refresh_keeps_search_and_clears_missing_detail(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/ui#/xuanjing") as page:
        _connect(page)
        page.locator(".xuanjing-node-item").filter(has_text="<img").dispatch_event("click")
        page.locator("#xuanjingSearchInput").fill("no matching memory")
        with page.expect_response(
            lambda r: "/graph/xuanjing?" in r.url and "lane=preference" in r.url
        ) as response:
            page.locator("#xuanjingLaneFilter").select_option("preference")
        assert response.value.status == 200
        assert [node["id"] for node in response.value.json()["nodes"]] == ["browser-beta"]
        expect(page.locator(".xuanjing-node-item")).to_have_count(1)
        expect(page.locator("#xuanjingDrawer")).to_be_hidden()
        expect(page.locator(".xuanjing-node-item")).to_have_css("opacity", "0.15")
        with page.expect_response(
            lambda r: "/graph/xuanjing?" in r.url and "limit=100" in r.url
        ) as response:
            page.locator("#xuanjingLimitFilter").select_option("100")
        assert response.value.status == 200


def test_observatory_empty_and_http_error_recovery(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/ui#/xuanjing") as page:
        _connect(page)
        page.locator("#xuanjingLaneFilter").select_option("session")
        expect(page.locator("#xuanjingNodeCount")).to_have_text("0")
        expect(page.locator(".xuanjing-node-item")).to_have_count(0)
        expect(page.locator("#xuanjingStatus")).to_contain_text("暂无记忆")
        page.evaluate("sessionStorage.setItem('lantai-api-key-session', 'invalid-key')")
        page.locator("#xuanjingRefreshBtn").click()
        expect(page.locator("#xuanjingStatus")).to_contain_text("受阻")
        expect(page.locator("#connectionDialog")).to_be_visible()
        page.locator("#connectionDialog").evaluate("dialog => dialog.close()")
        page.evaluate("sessionStorage.setItem('lantai-api-key-session', 'browser-synthetic-key')")
        page.locator("#xuanjingLaneFilter").select_option("all")
        expect(page.locator("#xuanjingNodeCount")).to_have_text("2")
        expect(page.locator(".xuanjing-node-item")).to_have_count(2)


@pytest.mark.parametrize("nested", [True, False])
def test_xuanjing_app_live_tool_hydration_theme_search_detail_and_empty(
    observatory, tmp_path, nested
):
    with browser_page(tmp_path, observatory + "/__test_host__") as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#node-count")).to_have_text("2")
        expect(frame.locator("#graph-svg .node-mark")).to_have_count(2)
        expect(frame.locator("#edge-count")).to_have_text("1")
        frame.locator('#node-list [data-node-id="browser-alpha"]').click()
        expect(frame.locator("#detail-content")).to_have_text(UNTRUSTED)
        assert (
            frame.locator("#selection-details img, #node-list img, #graph-svg image").count() == 0
        )
        assert frame.locator("body").evaluate("() => window.__xss === undefined")
        frame.locator('[data-edge-target="browser-beta"]').click()
        expect(frame.locator("#detail-id")).to_have_text("browser-beta")
        frame.locator("#node-search").fill("not found")
        expect(frame.locator("#node-list-empty")).to_have_text("当前切面没有匹配节点")
        frame.locator("#node-search").fill("")
        frame.locator("#lane-filter").select_option("fact")
        expect(frame.locator("#node-count")).to_have_text("1")
        expect(frame.locator("#selection-details")).to_be_hidden()
        page.evaluate("nested => window.sendContext({theme:'dark'}, nested)", nested)
        expect(frame.locator("html")).to_have_attribute("data-theme", "dark")
        expect(frame.locator("html")).to_have_css("color-scheme", "dark")
        assert (
            frame.locator("body").evaluate("e => getComputedStyle(e).backgroundColor")
            != "rgb(248, 250, 251)"
        )
        page.evaluate("nested => window.sendContext({theme:'light'}, nested)", nested)
        expect(frame.locator("html")).to_have_attribute("data-theme", "light")
        frame.locator("#theme-filter").fill("missing record")
        frame.locator("#theme-filter").press("Enter")
        expect(frame.locator("#node-count")).to_have_text("0")
        expect(frame.locator("#node-list-empty")).to_have_text("暂无记忆节点")
        expect(frame.locator("#timeline-empty")).to_be_visible()


def test_xuanjing_app_host_error_keeps_snapshot_and_recovers(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/__test_host__") as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#node-count")).to_have_text("2")
        page.evaluate("window.__hostError = '<img src=x onerror=window.__xss=true>宿主读取失败'")
        frame.locator("#refresh-button").click()
        expect(frame.locator("#app-status")).to_contain_text("宿主读取失败")
        assert frame.locator("#app-status img").count() == 0
        expect(frame.locator("#node-count")).to_have_text("2")
        expect(frame.locator("#refresh-button")).to_be_enabled()
        frame.locator("#refresh-button").click()
        expect(frame.locator("#app-status")).to_have_text("已读取 2 个节点、1 条关系边")
        page.evaluate("window.__hostResult = {structuredContent:{nodes:'bad',edges:[]}}")
        frame.locator("#refresh-button").click()
        expect(frame.locator("#app-status")).to_contain_text("不符合悬镜图谱结构")
        expect(frame.locator("#graph-svg .node-mark")).to_have_count(2)


def test_xuanjing_app_without_tool_capability_does_not_request_refresh(observatory, tmp_path):
    with browser_page(tmp_path, observatory + "/__test_host__?server_tools=false") as page:
        frame = page.frame_locator("iframe")
        expect(frame.locator("#node-count")).to_have_text("2")
        expect(frame.locator("#refresh-button")).to_be_disabled()
        expect(frame.locator("#lane-filter")).to_be_disabled()
        assert page.evaluate("window.__calls.length") == 1
