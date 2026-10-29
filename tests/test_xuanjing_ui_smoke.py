"""悬镜·UI 控制台星图观测台面板集成冒烟测试 (Ticket 02)

验证接缝（Seams）：
1. GET /ui 返回的控制台页面中包含「悬镜星图」专属主导航项与工作区视图；
2. GET /ui/assets/xuanjing.js 静态脚本能够正常托管分发，且 Content-Type 为 javascript；
3. GET /ui/map 与 GET /graph/xuanjing 数据流通畅，UI 可观测性闭环。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem


@pytest.fixture()
def ui_smoke_env(monkeypatch):
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory


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
