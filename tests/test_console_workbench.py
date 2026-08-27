"""新案牍控制台静态资源与旧路由兼容。"""
from fastapi.testclient import TestClient

from api_server import app


def test_new_console_and_modules_are_served():
    with TestClient(app) as client:
        page = client.get("/ui")
        assert page.status_code == 200
        assert "兰台 · 案牍" in page.text
        assert "/ui/assets/app.js" in page.text
        assert client.get("/ui/assets/styles.css").status_code == 200
        assert client.get("/ui/assets/app.js").status_code == 200


def test_legacy_console_routes_remain_available():
    with TestClient(app) as client:
        for path in ("/ui/recall", "/ui/evolve", "/ui/pulse", "/ui/vault", "/ui/map"):
            assert client.get(path).status_code == 200

