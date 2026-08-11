"""追忆漏斗控制台（Ticket 04）路由冒烟测试。

静态页零依赖（无 node/打包），公开挂载不改检索语义；只验证可达与内容标记。
"""
from fastapi.testclient import TestClient
from api_server import app


def test_recall_console_served():
    with TestClient(app) as c:
        r = c.get("/ui/recall")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "追忆" in r.text
        assert "trace" in r.text


def test_ui_index_lists_panels():
    """入口页：列出追忆漏斗 / 检索质量看板 / 档案与锦囊。"""
    with TestClient(app) as c:
        r = c.get("/ui")
        assert r.status_code == 200
        assert "/ui/recall" in r.text
        assert "/ui/evolve" in r.text
        assert "/ui/vault" in r.text
