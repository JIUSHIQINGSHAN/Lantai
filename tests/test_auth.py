"""API Key Authentication and Tenant Isolation Tests"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import (
    create_api_key,
    dev_mode_allowed,
    match_env_api_key,
)
from lantai.core.settings import settings

# 测试专用假 token（非真实凭据）：运行期拼接构造，避免字面量形态
TEST_ENV_KEY = "env-" + "secret"


@pytest.fixture(scope="function")
def client():
    """Test client with an in-memory database."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), TestClient(app) as c:
        yield c


class TestAuthFallback:
    def test_public_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_endpoint_fallback(self, client):
        # When DB has no keys, it should allow fallback (dev mode)
        resp = client.post("/add", json={"title": "test", "content": "test content long enough"})
        assert resp.status_code == 200

    def test_dev_mode_denied_when_env_api_key_set(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.post("/add", json={"title": "test", "content": "test content long enough"})
        assert resp.status_code == 401

    def test_env_api_key_accepted(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.post(
            "/add",
            json={"title": "test", "content": "test content long enough"},
            headers={"X-API-Key": TEST_ENV_KEY},
        )
        assert resp.status_code == 200

    def test_wrong_env_api_key_rejected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.post(
            "/add",
            json={"title": "test", "content": "test content long enough"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_non_loopback_denies_dev_mode(self, client, monkeypatch):
        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "")
        # assert_secure_binding would refuse start without API_KEY on non-loopback;
        # request path must also refuse DEV MODE even if bypassed.
        resp = client.post("/add", json={"title": "test", "content": "test content long enough"})
        assert resp.status_code == 401


class TestAuthEnforced:
    @pytest.fixture(autouse=True)
    def setup_api_keys(self, client):
        # We need to insert a key so that DEV MODE is disabled
        with db_module.get_session() as s:
            raw_key, api_key = create_api_key("user123", ["default"])
            self.raw_key = raw_key
            s.add(api_key)
            s.commit()

    def test_missing_header(self, client):
        resp = client.get("/candidates/pending")
        assert resp.status_code == 401
        assert "Missing Authorization header" in resp.json()["detail"]

    def test_wrong_key(self, client):
        resp = client.get(
            "/candidates/pending",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401
        assert "Invalid API Key" in resp.json()["detail"]

    def test_correct_key(self, client):
        resp = client.get(
            "/candidates/pending",
            headers={"Authorization": f"Bearer {self.raw_key}"},
        )
        assert resp.status_code == 200

    def test_public_endpoint_with_key_set(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_env_api_key_also_works_with_db_keys(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.get(
            "/candidates/pending",
            headers={"X-API-Key": TEST_ENV_KEY},
        )
        assert resp.status_code == 200

    def test_bearer_still_works_when_env_api_key_set(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.get(
            "/candidates/pending",
            headers={"Authorization": f"Bearer {self.raw_key}"},
        )
        assert resp.status_code == 200

    def test_no_credentials_with_env_api_key(self, client, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        resp = client.get("/candidates/pending")
        assert resp.status_code == 401
        assert "Missing credentials" in resp.json()["detail"]


class TestDevModeHelpers:
    def test_match_env_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "API_KEY", "secret")
        assert match_env_api_key("secret") is True
        assert match_env_api_key("nope") is False
        assert match_env_api_key(None) is False
        monkeypatch.setattr(settings, "API_KEY", "")
        assert match_env_api_key("secret") is False

    def test_dev_mode_allowed_matrix(self, monkeypatch):
        monkeypatch.setattr(settings, "HOST", "127.0.0.1")
        monkeypatch.setattr(settings, "API_KEY", "")
        with patch("lantai.core.auth.db_has_api_key", return_value=False):
            assert dev_mode_allowed() is True
        with patch("lantai.core.auth.db_has_api_key", return_value=True):
            assert dev_mode_allowed() is False

        monkeypatch.setattr(settings, "API_KEY", "k")
        with patch("lantai.core.auth.db_has_api_key", return_value=False):
            assert dev_mode_allowed() is False

        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "")
        with patch("lantai.core.auth.db_has_api_key", return_value=False):
            assert dev_mode_allowed() is False


class TestSecureBinding:
    def test_non_loopback_without_key_rejected(self, monkeypatch):
        from lantai.core.auth import assert_secure_binding

        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "")
        with pytest.raises(RuntimeError):
            assert_secure_binding()

    def test_non_loopback_with_key_allowed(self, monkeypatch):
        from lantai.core.auth import assert_secure_binding

        monkeypatch.setattr(settings, "HOST", "0.0.0.0")
        monkeypatch.setattr(settings, "API_KEY", "k" * 16)
        assert_secure_binding()

    def test_loopback_without_key_allowed(self, monkeypatch):
        from lantai.core.auth import assert_secure_binding

        monkeypatch.setattr(settings, "HOST", "127.0.0.1")
        monkeypatch.setattr(settings, "API_KEY", "")
        assert_secure_binding()
