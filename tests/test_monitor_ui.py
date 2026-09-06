"""司天（ADR-0044）前端契约测试：静态资源可达 + 接口引用 + DOM 选择器一致性。

前端无构建链（ADR-0025），没有 JS 单测跑器，因此把「界面与后端契约」压成静态断言：
- 页面/资源可达且 MIME 正确；
- monitor.js 引用的接口路径与 routes_monitor.py 暴露的一致；
- 所有 `$('#id')` 选择器在 index.html 中真实存在——这条正是为了防住
  app.js 曾引用不存在的 `#systemRefresh` 导致 `init()` 抛错、整个控制台失效的回归。
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from api_server import app

UI_DIR = Path(__file__).resolve().parent.parent / "lantai" / "api" / "ui"
INDEX_HTML = (UI_DIR / "index.html").read_text(encoding="utf-8")


def test_ui_serves_monitor_view():
    with TestClient(app) as client:
        page = client.get("/ui")
        assert page.status_code == 200
        assert 'id="monitorView"' in page.text
        assert "司天" in page.text
        assert 'data-view="monitor"' in page.text
        assert 'id="navMonitorCount"' in page.text

        asset = client.get("/ui/assets/monitor.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert "monitor/overview" in asset.text


def test_monitor_js_references_real_endpoints():
    source = (UI_DIR / "monitor.js").read_text(encoding="utf-8")
    for path in ("/monitor/overview", "/monitor/series", "/monitor/logs",
                 "/monitor/config", "/monitor/workers/"):
        assert path in source, f"monitor.js 未引用 {path}"

    from lantai.api.routes_monitor import router

    exposed = {route.path for route in router.routes}
    assert {"/monitor/overview", "/monitor/series", "/monitor/logs", "/monitor/config",
            "/monitor/prometheus", "/monitor/workers/{worker_name}/run"} <= exposed


def test_every_dom_selector_exists_in_index_html():
    """JS 里的 `$('#id')` / `getElementById('id')` 必须能在 index.html 或 JS 中找到。

    案牍详情抽屉里的字段（如 crystalSteps / treePath）由 JS 动态创建后再读，
    因此把 JS 内 `.id = '...'` 赋值的 id 也算作已定义。
    """
    ids = set(re.findall(r'id="([^"]+)"', INDEX_HTML))
    missing: dict[str, list[str]] = {}
    for name in ("app.js", "monitor.js", "terminal.js"):
        source = (UI_DIR / name).read_text(encoding="utf-8")
        defined = ids | set(re.findall(r"""\.id\s*=\s*['"]([A-Za-z0-9_-]+)['"]""", source))
        wanted = set(re.findall(r"""\$\(\s*['"]#([A-Za-z0-9_-]+)['"]""", source))
        wanted |= set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]""", source))
        absent = sorted(wanted - defined)
        if absent:
            missing[name] = absent
    assert not missing, f"以下选择器在 index.html 中不存在：{missing}"


def test_monitor_view_panels_present():
    for marker in ('id="monitorMetrics"', 'id="monitorAlerts"', 'id="monitorDeps"',
                   'id="monitorChart"', 'id="monitorEndpoints"', 'id="monitorWorkers"',
                   'id="monitorPipeline"', 'id="monitorLogs"', 'id="monitorConfig"',
                   'id="monitorRefreshBtn"', 'id="monitorAutoRefresh"'):
        assert marker in INDEX_HTML, f"监控面板缺少 {marker}"
