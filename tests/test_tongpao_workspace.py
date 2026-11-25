"""同袍工作区通过公共服务接口验证真实存储与隔离。"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from lantai.core.acl import Principal
from lantai.core.auth import create_api_key, get_current_user
from lantai.core.settings import settings
from lantai.retrieval.hybrid import hybrid_search
from lantai.services.tongpao_workspace import (
    mount_memories,
    publish_shared_memory,
    store_private_memory,
)


def _agent(agent_id="agent-a", tenant_id="tenant-1", user_id="user-1"):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        allowed_lanes=["general"],
    )


def test_private_memory_persists_only_in_its_workspace(param_env):
    owner = _agent()
    stored = store_private_memory("Only agent A may read this working state.", principal=owner)
    assert [row.id for row in mount_memories(principal=owner)] == [stored.id]
    assert mount_memories(principal=_agent("agent-b")) == ()
    assert mount_memories(principal=_agent(tenant_id="tenant-2")) == ()
    assert mount_memories(principal=_agent(user_id="user-2")) == ()


def test_bound_key_supplies_identity_and_rejects_spoofing(param_env, monkeypatch):
    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    raw, key = create_api_key("user-1", ["general"], tenant_id="tenant-1", agent_id="agent-a")
    with factory() as session:
        session.add(key)
        session.commit()

    def request(**extra):
        headers = {"authorization": f"Bearer {raw}", **extra}
        return Request(
            {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
        )

    principal = get_current_user(request())
    assert (principal.tenant_id, principal.user_id, principal.agent_id) == (
        "tenant-1",
        "user-1",
        "agent-a",
    )
    assert principal.workspace_bound
    for extra in ({"x-agent-id": "agent-b"}, {"x-tenant-id": "tenant-2"}):
        with pytest.raises(HTTPException) as error:
            get_current_user(request(**extra))
        assert error.value.status_code == 403


def test_standard_search_cannot_bypass_workspace_by_omitting_headers(param_env):
    stored = store_private_memory("workspaceisolationtoken", principal=_agent())
    for viewer in (
        Principal(user_id="user-1"),
        Principal(user_id="user-1", agent_id="agent-a"),
        Principal(user_id="user-1", tenant_id="tenant-1"),
    ):
        results = hybrid_search("workspaceisolationtoken", principal=viewer, memory_types=["text"])
        assert stored.id not in {row["memory"]["id"] for row in results}


def test_shared_mount_is_read_only_and_confined_to_tenant_and_user(param_env):
    from dataclasses import FrozenInstanceError

    owner = _agent()
    shared = publish_shared_memory(
        "Verified shared operating procedure.",
        publisher=Principal(role="system"),
        workspace=owner,
    )
    private = store_private_memory("Keep this local.", principal=owner)
    viewer = _agent("agent-b")
    mounted = mount_memories(principal=viewer)
    assert [row.id for row in mounted] == [shared.id]
    with pytest.raises(FrozenInstanceError):
        mounted[0].content = "Overwrite public knowledge"
    assert mount_memories(principal=viewer)[0].content == "Verified shared operating procedure."
    assert [row.id for row in mount_memories(principal=owner, shared_scope="private")] == [
        private.id
    ]
    assert [row.id for row in mount_memories(principal=owner, shared_scope="swarm_shared")] == [
        shared.id
    ]
    assert mount_memories(principal=_agent("agent-b", tenant_id="tenant-2")) == ()
    assert mount_memories(principal=_agent("agent-b", user_id="user-2")) == ()
    with pytest.raises(HTTPException) as error:
        publish_shared_memory("Unreviewed public write", publisher=viewer, workspace=viewer)
    assert error.value.status_code == 403


@pytest.mark.parametrize("principal", [None, Principal(), Principal(user_id="user-1")])
def test_missing_workspace_identity_is_rejected(param_env, principal):
    for action in (
        lambda: store_private_memory("Do not store this.", principal=principal),
        lambda: mount_memories(principal=principal),
    ):
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 403


def test_query_lane_and_limit_apply_after_workspace_isolation(param_env):
    owner = _agent()
    stored = store_private_memory("literal %_ workspace token", principal=owner)
    for index in range(3):
        store_private_memory(f"other workspace {index}", principal=_agent("agent-b"))
    assert [row.id for row in mount_memories(principal=owner, limit=1)] == [stored.id]
    assert [row.id for row in mount_memories(principal=owner, query="%_")] == [stored.id]
    assert mount_memories(principal=owner, query="%missing") == ()
    assert mount_memories(principal=Principal(**{**owner.__dict__, "allowed_lanes": []})) == ()
    with pytest.raises(HTTPException):
        store_private_memory("Forbidden lane write", principal=owner, lane="secret")
    for limit in (0, 1001, True):
        with pytest.raises(ValueError):
            mount_memories(principal=owner, limit=limit)
    with pytest.raises(ValueError):
        mount_memories(principal=owner, shared_scope="unknown")


def test_workspace_routes_use_real_bound_keys_and_admin_publication(param_env, monkeypatch):
    from lantai.api.app import app

    factory, _ = param_env
    monkeypatch.setattr(settings, "API_KEY", "workspace-admin-test")
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})

    def headers(agent, *, bound=True):
        raw, key = create_api_key(
            "user-1",
            ["general"],
            **({"tenant_id": "tenant-1", "agent_id": agent} if bound else {}),
        )
        with factory() as session:
            session.add(key)
            session.commit()
        return {"Authorization": f"Bearer {raw}"}

    a, b, legacy = headers("agent-a"), headers("agent-b"), headers("agent-a", bound=False)
    client = TestClient(app)
    created = client.post("/tongpao/private", headers=a, json={"content": "Private working state"})
    assert created.status_code == 200, created.text
    memory_id = created.json()["id"]
    assert client.get("/tongpao/memories", headers=a).json()["memories"][0]["id"] == memory_id
    assert client.get("/tongpao/memories", headers=b).json() == {"memories": []}
    assert (
        client.get("/tongpao/memories", headers={**a, "X-Agent-Id": "agent-b"}).status_code == 403
    )
    assert client.get("/tongpao/memories", headers=legacy).status_code == 403
    assert client.get("/tongpao/memories").status_code == 401
    assert (
        client.post(
            "/tongpao/shared",
            headers=a,
            json={
                "content": "Unapproved",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "agent_id": "agent-a",
            },
        ).status_code
        == 403
    )
    shared = client.post(
        "/tongpao/shared",
        headers={"X-API-Key": "workspace-admin-test"},
        json={
            "content": "Reviewed shared procedure",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "agent_id": "agent-a",
        },
    )
    assert shared.status_code == 200, shared.text
    shared_id = shared.json()["id"]
    assert [row["id"] for row in client.get("/tongpao/memories", headers=b).json()["memories"]] == [
        shared_id
    ]
    assert client.put(
        f"/tongpao/shared/{shared_id}", headers=b, json={"content": "tamper"}
    ).status_code in (404, 405)
    assert (
        client.post(
            "/tongpao/private", headers=a, json={"content": "fake", "agent_id": "agent-b"}
        ).status_code
        == 422
    )


def test_workspace_key_cannot_expand_lanes_through_agent_binding(param_env, monkeypatch):
    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["general", "secret"]})
    raw, key = create_api_key("user-1", ["general"], tenant_id="tenant-1", agent_id="agent-a")
    with factory() as session:
        session.add(key)
        session.commit()
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {raw}".encode())]})
    assert get_current_user(request).allowed_lanes == ["general"]


def test_shared_lanes_are_filtered_before_limit(param_env):
    owner = _agent()
    shared = publish_shared_memory(
        "Visible shared procedure",
        publisher=Principal(role="system"),
        workspace=owner,
    )
    publish_shared_memory(
        "Restricted shared procedure",
        publisher=Principal(role="system"),
        workspace=Principal(tenant_id="tenant-1", user_id="user-1", agent_id="agent-a"),
        lane="secret",
    )
    assert [row.id for row in mount_memories(principal=_agent("agent-b"), limit=1)] == [shared.id]


def test_empty_key_lane_grant_stays_empty(param_env, monkeypatch):
    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    raw, key = create_api_key("user-1", [], tenant_id="tenant-1", agent_id="agent-a")
    with factory() as session:
        session.add(key)
        session.commit()
    principal = get_current_user(
        Request({"type": "http", "headers": [(b"authorization", f"Bearer {raw}".encode())]})
    )
    assert principal.allowed_lanes == []
    with pytest.raises(HTTPException) as error:
        store_private_memory("Not permitted", principal=principal)
    assert error.value.status_code == 403


def test_legacy_key_retains_deployment_lane_binding(param_env, monkeypatch):
    from lantai.api.app import app

    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["general"]})
    raw, key = create_api_key("user-1", ["default"])
    with factory() as session:
        session.add(key)
        session.commit()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {raw}", "X-Agent-Id": "agent-a"}
    response = client.post(
        "/add/raw",
        headers=headers,
        json={"content": "Legacy binding remains valid", "lane": "general"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/tongpao/memories", headers=headers).status_code == 403


def test_workspace_rejects_conflicting_user_header(param_env, monkeypatch):
    from lantai.api.app import app

    factory, _ = param_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    raw, key = create_api_key("user-1", ["general"], tenant_id="tenant-1", agent_id="agent-a")
    with factory() as session:
        session.add(key)
        session.commit()
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {raw}", "X-User-Id": "user-2"}
        response = client.post(
            "/tongpao/private", headers=headers, json={"content": "Reject spoofing"}
        )
        assert response.status_code == 403
        assert client.get(
            "/tongpao/memories", headers={"Authorization": f"Bearer {raw}"}
        ).json() == {"memories": []}


def test_multi_agent_concurrent_reads_and_writes_use_real_sqlite(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'workspaces.db').as_posix()}", connect_args={"timeout": 30}
    )
    SQLModel.metadata.create_all(engine)
    barrier = Barrier(4)
    try:
        with Session(engine) as session:
            shared = publish_shared_memory(
                "Shared procedure for the first tenant.",
                publisher=Principal(role="system"),
                workspace=_agent(),
                session=session,
            )

        def worker(index):
            principal = _agent(f"agent-{index}", tenant_id="tenant-1" if index < 3 else "tenant-2")
            barrier.wait(timeout=10)
            ids = set()
            for step in range(8):
                with Session(engine) as session:
                    result = store_private_memory(
                        f"agent {index} step {step}", principal=principal, session=session
                    )
                    ids.add(result.id)
                    mounted = mount_memories(principal=principal, session=session)
                    assert all(
                        row.agent_id == principal.agent_id or row.id == shared.id for row in mounted
                    )
                    assert all(row.tenant_id == principal.tenant_id for row in mounted)
            with Session(engine) as session:
                mounted = mount_memories(principal=principal, session=session)
            expected = ids | ({shared.id} if index < 3 else set())
            assert {row.id for row in mounted} == expected
            return ids

        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(worker, range(4)))
        assert len(set.union(*groups)) == 32
    finally:
        engine.dispose()


def test_workspace_migration_preserves_old_keys_and_is_idempotent():
    import sqlite3

    from lantai.storage.migrations import TARGET_SCHEMA_VERSION, apply_migrations

    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE api_keys (id VARCHAR PRIMARY KEY, key_hash VARCHAR, user_id VARCHAR)"
        )
        connection.execute("INSERT INTO api_keys VALUES ('legacy', 'unchanged-hash', 'user-1')")
        connection.execute("PRAGMA user_version = 47")
        apply_migrations(connection)
        apply_migrations(connection)
        assert connection.execute("SELECT * FROM api_keys").fetchone() == (
            "legacy",
            "unchanged-hash",
            "user-1",
            None,
            None,
        )
        assert connection.execute("SELECT count(*) FROM tongpao_workspace_memory").fetchone() == (
            0,
        )
        assert connection.execute("PRAGMA user_version").fetchone() == (TARGET_SCHEMA_VERSION,)
