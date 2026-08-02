"""API Key 鉴权测试"""
import pytest
from unittest.mock import patch
from api_server import app
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import remembrance.storage.db as db_module
from remembrance.core.settings import settings


@pytest.fixture(scope="function")
def client():
    """创建测试客户端，使用内存数据库"""
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session):
        with TestClient(app) as c:
            yield c


class TestAuthDisabled:
    """API Key 鉴权关闭时（默认）"""

    def test_public_endpoint_no_key(self, client):
        """公共端点不需要 key"""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_endpoint_no_key(self, client):
        """业务端点不需要 key（鉴权关闭）"""
        resp = client.post("/add", json={"title": "test", "content": "test content long enough"})
        assert resp.status_code == 200


class TestAuthEnabled:
    """API Key 鉴权开启时"""

    @patch.object(settings, "API_KEY", "test-key")
    def test_missing_key(self, client):
        """缺少 X-API-Key → 401"""
        resp = client.post("/add", json={"title": "t", "content": "test content"})
        assert resp.status_code == 401

    @patch.object(settings, "API_KEY", "test-key")
    def test_wrong_key(self, client):
        """错误的 X-API-Key → 403"""
        resp = client.post(
            "/add",
            json={"title": "t", "content": "test content"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 403

    @patch.object(settings, "API_KEY", "test-key")
    def test_correct_key(self, client):
        """正确的 X-API-Key → 200"""
        resp = client.post(
            "/add",
            json={"title": "test", "content": "test content long enough"},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200

    @patch.object(settings, "API_KEY", "test-key")
    def test_public_endpoint_with_key_set(self, client):
        """公共端点即使设置了 key也不需要"""
        resp = client.get("/health")
        assert resp.status_code == 200


class TestSecureBinding:
    """P0-2: 非回环绑定必须配置 API_KEY"""

    def test_non_loopback_without_key_rejected(self, monkeypatch):
        from remembrance.core.auth import assert_secure_binding
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "")
        with pytest.raises(RuntimeError):
            assert_secure_binding()

    def test_non_loopback_with_key_allowed(self, monkeypatch):
        from remembrance.core.auth import assert_secure_binding
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "k" * 16)
        assert_secure_binding()  # 不抛异常

    def test_loopback_without_key_allowed(self, monkeypatch):
        from remembrance.core.auth import assert_secure_binding
        monkeypatch.setattr(settings, "HOST", "127.0.0.1")
        monkeypatch.setattr(settings, "API_KEY", "")
        assert_secure_binding()
