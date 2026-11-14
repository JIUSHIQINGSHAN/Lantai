r"""悬镜（ADR-0038）：兰台可视化管理控制台（Lantai Studio）测试。

验证：
1. GET /ui 返回完整 Studio HTML 页面（包含案牍、器识、札记、沉潜、演练场与星图导航）；
2. GET /ui/assets/styles.css 与 GET /ui/assets/app.js 正常响应；
3. Studio 联动接口连通性（/persona, /scratchpad, /evolution/consolidate, /probing/detect）。
"""

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
from fastapi.testclient import TestClient

from lantai.api.app import app

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
    pytest.fail("Chromium is required for the Studio layout regression; set LANTAI_TEST_CHROMIUM")


# 探针页放进定宽 iframe 里量（巡检 2026-11-14）：完整版 Chrome 的新 headless
# 把 --window-size 的宽度钳到 >= 500px（CI ubuntu-latest 的 google-chrome 即此，
# 320 档实测 innerWidth=500），只有 chrome-headless-shell 认 320。iframe 的视口
# 宽度就是 iframe 自身宽度，媒体查询按它求值，与浏览器窗口最小宽度无关。
_OVERFLOW_STYLE = "<style>.studio-grid{min-width:480px}</style>"


def _host_page(width: int, frame: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0}iframe{display:block;border:0}</style>"
        f"</head><body><iframe id='probe' src='/ui/{frame}' width='{width}' height='900'"
        f" style='width:{width}px;height:900px'></iframe></body></html>"
    ).encode()


@pytest.fixture()
def studio_layout_server() -> Iterator[str]:
    """Serve real UI markup and styles without business API calls or user data."""
    markup = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", markup, flags=re.S | re.I)
    probe = """
<script>
window.addEventListener('load', () => {
  document.querySelectorAll('.view').forEach(view => {
    view.classList.toggle('active', view.id === 'studioView');
  });
  const measurements = ['panelRecall', 'panelPrompts'].map(id => {
    document.querySelectorAll('.studio-tab-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === id);
      panel.hidden = panel.id !== id;
    });
    const grid = document.querySelector('#' + id + ' .studio-grid');
    const tracks = getComputedStyle(grid).gridTemplateColumns.split(/\\s+/).map(Number.parseFloat);
    return {
      panel: id,
      viewport: innerWidth,
      columns: tracks.filter(width => width > 0).length,
      gridWidth: grid.getBoundingClientRect().width,
      scrollWidth: document.documentElement.scrollWidth,
      cards: [...grid.children].map(card => card.getBoundingClientRect().width)
    };
  });
  // 结果写回宿主页：--dump-dom 只导出顶层文档。iframe 的 load 先于宿主页
  // load 触发，宿主页 load 后导出时结果已在。
  const target = window.parent !== window ? window.parent.document : document;
  const result = target.createElement('pre');
  result.id = 'layout-result';
  result.hidden = true;
  result.textContent = JSON.stringify(measurements);
  target.body.append(result);
});
</script>
"""
    frames = {
        "frame": markup.replace("</body>", probe + "</body>").encode("utf-8"),
        # 对照页：故意让网格在窄屏溢出，证明探针在 320 档真能抓到横向溢出。
        "frame-overflow": markup.replace("</head>", _OVERFLOW_STYLE + "</head>")
        .replace("</body>", probe + "</body>")
        .encode("utf-8"),
    }

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(_UI_DIR), **kwargs)

        def _send_html(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path == "/ui/host":
                query = parse_qs(parts.query)
                frame = query.get("frame", ["frame"])[0]
                if frame not in frames:
                    self.send_error(404)
                    return
                self._send_html(_host_page(int(query["w"][0]), frame))
            elif parts.path.removeprefix("/ui/") in frames:
                self._send_html(frames[parts.path.removeprefix("/ui/")])
            else:
                self.path = self.path.removeprefix("/ui/assets")
                super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ui"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _measure_layout(tmp_path: Path, base_url: str, width: int, frame: str = "frame") -> list:
    proc = subprocess.run(
        [
            _chromium_executable(),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-proxy-server",
            "--disable-background-networking",
            "--force-device-scale-factor=1",
            f"--window-size={width},1000",
            f"--user-data-dir={tmp_path / f'chromium-profile-{frame}'}",
            "--dump-dom",
            f"{base_url}/host?w={width}&frame={frame}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    match = re.search(r'<pre id="layout-result"[^>]*>(.*?)</pre>', proc.stdout, flags=re.S)
    assert match, "Chromium did not produce real layout measurements"
    measurements = json.loads(html.unescape(match.group(1)))
    assert {row["panel"] for row in measurements} == {"panelRecall", "panelPrompts"}
    return measurements


@pytest.mark.parametrize(("width", "columns"), [(320, 1), (1440, 2)])
def test_studio_layout_in_real_chromium(
    tmp_path: Path, studio_layout_server: str, width: int, columns: int
) -> None:
    """Real CSS layout must fit 320px and preserve the desktop grid."""
    measurements = _measure_layout(tmp_path, studio_layout_server, width)
    for row in measurements:
        assert row["viewport"] == width, row
        assert row["columns"] == columns, row
        assert row["scrollWidth"] <= width, row
        assert row["gridWidth"] > 0 and all(card > 0 for card in row["cards"]), row


def test_studio_layout_probe_catches_overflow_at_320(
    tmp_path: Path, studio_layout_server: str
) -> None:
    """对照组：网格被强行撑到 480px 时，320 档必须量出横向溢出（探针不是摆设）。"""
    measurements = _measure_layout(tmp_path, studio_layout_server, 320, "frame-overflow")
    for row in measurements:
        assert row["viewport"] == 320, row
        assert row["scrollWidth"] > 320, row


class TestStudioUI:
    """悬镜控制台静态页面与接口测试。"""

    def test_ui_index_html_renders_studio(self, param_env):
        client = TestClient(app)
        resp = client.get("/ui")
        assert resp.status_code == 200
        text = resp.text
        assert "兰台" in text
        assert "器识" in text or "人格" in text
        assert "札记" in text or "便签" in text
        assert "沉潜" in text
        assert "演练" in text or "检索" in text
        assert 'id="mainContent"' in text
        assert 'class="skip-link"' in text
        assert 'aria-label="移动端主导航"' in text

    def test_ui_assets_serve_properly(self, param_env):
        client = TestClient(app)
        r_css = client.get("/ui/assets/styles.css")
        assert r_css.status_code == 200
        assert "text/css" in r_css.headers.get("content-type", "")

        r_js = client.get("/ui/assets/app.js")
        assert r_js.status_code == 200
        assert "javascript" in r_js.headers.get("content-type", "")

    def test_studio_backend_apis(self, param_env):
        client = TestClient(app)
        # 1. 验证器识接口
        r_per = client.get("/persona")
        assert r_per.status_code == 200
        # 2. 验证札记接口
        r_sc = client.get("/scratchpad?session_id=studio_test")
        assert r_sc.status_code == 200
        # 3. 验证沉潜报告接口
        r_con = client.get("/evolution/consolidate/report")
        assert r_con.status_code == 200
        # 4. 验证探针检测接口
        r_prob = client.post("/probing/detect", json={"query": "测试"})
        assert r_prob.status_code == 200
