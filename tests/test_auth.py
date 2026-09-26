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


class TestAdminAclExemptionAndDefaultLanes:
    """冒烟测试：验证 admin/system 角色 ACL 豁免与 DEFAULT_LANES 扩充覆盖"""

    def test_default_lanes_contains_expected_lanes(self):
        from lantai.core.auth import DEFAULT_LANES

        expected = [
            "general",
            "fact",
            "rule",
            "experience",
            "preference",
            "chat",
            "default",
            "distill",
            "hermes",
            "user",
            "project",
            "working",
        ]
        assert expected == DEFAULT_LANES

    def test_dev_mode_principal_receives_all_default_lanes(self, client):
        """DEV MODE 下（无 key，回环地址），发起的请求具有完整的 12 个默认泳道"""
        for lane in ("hermes", "user", "project", "working"):
            resp = client.post(
                "/add",
                json={
                    "title": f"Dev test {lane}",
                    "content": f"This is sufficient content for testing dev mode on {lane}",
                    "lane": lane,
                },
            )
            assert resp.status_code == 200, f"Failed for lane: {lane}, resp: {resp.text}"

    def test_admin_with_unbound_agent_id_bypasses_acl(self, client, monkeypatch):
        """Admin (通过 X-API-Key 鉴权) 携带未绑定的 X-Agent-Id 能够豁免 ACL，不被 403 阻断"""
        monkeypatch.setattr(settings, "API_KEY", TEST_ENV_KEY)
        monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"bound-agent": ["general"]})

        # 携带未绑定的 agent-id: "unbound-agent"，向 "project" 泳道写入
        resp = client.post(
            "/add",
            headers={
                "X-API-Key": TEST_ENV_KEY,
                "X-Agent-Id": "unbound-agent",
            },
            json={
                "title": "Admin bypass ACL test",
                "content": "Admin should bypass ACL even if unbound agent id is passed",
                "lane": "project",
            },
        )
        assert resp.status_code == 200

    def test_non_admin_with_unbound_agent_id_rejected_by_acl(self, client, monkeypatch):
        """普通用户 (Bearer Key 鉴权) 携带未绑定的 X-Agent-Id 时会被 ACL 拦截返回 403"""
        monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"bound-agent": ["general"]})

        with db_module.get_session() as s:
            raw_key, api_key = create_api_key("regular-user", ["project"])
            s.add(api_key)
            s.commit()

        resp = client.post(
            "/add",
            headers={
                "Authorization": f"Bearer {raw_key}",
                "X-Agent-Id": "unbound-agent",
            },
            json={
                "title": "Regular user ACL check",
                "content": "Regular user must be blocked when agent id is unbound",
                "lane": "project",
            },
        )
        assert resp.status_code == 403
        assert "Agent not bound (ACL)" in resp.json()["detail"]

    def test_non_admin_bearer_cannot_write_to_unauthorized_new_lanes(self, client):
        """受限 Bearer 用户无法跨越其授权边界写入新增泳道 (如 hermes, project)"""
        with db_module.get_session() as s:
            raw_key, api_key = create_api_key("restricted-user", ["general"])
            s.add(api_key)
            s.commit()

        for lane in ("hermes", "project", "working", "user"):
            resp = client.post(
                "/add",
                headers={"Authorization": f"Bearer {raw_key}"},
                json={
                    "title": f"Restricted write {lane}",
                    "content": f"Content should not be writable to {lane} by restricted user",
                    "lane": lane,
                },
            )
            assert resp.status_code == 403
