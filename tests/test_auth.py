"""API Key Authentication and Tenant Isolation Tests"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from api_server import app
from lantai.core.settings import settings
from lantai.models.tables import ApiKey
from lantai.core.auth import hash_key, create_api_key

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
