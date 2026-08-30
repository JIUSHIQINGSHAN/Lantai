r"""悬镜（ADR-0038）：兰台可视化管理控制台（Lantai Studio）测试。

验证：
1. GET /ui 返回完整 Studio HTML 页面（包含案牍、器识、札记、沉潜、演练场与星图导航）；
2. GET /ui/assets/styles.css 与 GET /ui/assets/app.js 正常响应；
3. Studio 联动接口连通性（/persona, /scratchpad, /evolution/consolidate, /probing/detect）。
"""
import pytest
from fastapi.testclient import TestClient

from api_server import app


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
