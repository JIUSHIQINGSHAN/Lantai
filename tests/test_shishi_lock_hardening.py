"""石室不可变锁必须约束所有既有核心记忆写入口。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from lantai.cognition import shishi as shishi_module
from lantai.cognition.shishi import ShishiEngine, ShishiVaultBlock
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import CoreMemoryBlock
from lantai.services import memory_service
from lantai.services.memory_service import put_core_memory


def test_shishi_vault_block_contract_is_immutable():
    block = ShishiVaultBlock(
        block_key="policy",
        content="封存规则",
        content_digest="0" * 64,
        version=1,
        is_immutable=True,
        namespace="shishi-contract-test",
        fenced_content="<memory_data>封存规则</memory_data>",
    )

    with pytest.raises(FrozenInstanceError):
        block.content = "被修改的规则"


def test_sha256_digest_matches_known_vector():
    from lantai.cognition.shishi import compute_content_digest

    assert (
        compute_content_digest("policy", "known-content", "default")
        == "cfeed41b2745b17987cde18f10b4132ac9e17ffdaa391af613027703914e74ad"
    )


def test_put_core_memory_cannot_overwrite_immutable_shishi_block(param_env):
    principal = Principal(user_id="shishi-owner", tenant_id="tenant-a")
    engine = ShishiEngine(principal=principal)
    engine.seal_core_memory(
        "policy",
        "锁定后的核心规则",
        namespace="shishi-lock-test",
        is_immutable=True,
    )

    with pytest.raises(PermissionError, match="sealed in Shishi vault and immutable"):
        put_core_memory(
            "policy",
            "绕过石室的改写",
            namespace="shishi-lock-test",
            principal=principal,
        )

    report = engine.verify_integrity(namespace="shishi-lock-test")
    blocks = engine.get_sealed_blocks(namespace="shishi-lock-test")
    assert report["is_valid"] is True
    assert report["verified_blocks"] == 1
    assert len(blocks) == 1
    assert blocks[0]["content"] == "锁定后的核心规则"


def test_core_memory_route_returns_forbidden_for_immutable_shishi_block(param_env):
    principal = Principal(user_id="shishi-route-owner", tenant_id="tenant-a")
    namespace = "shishi-route-lock-test"
    ShishiEngine(principal=principal).seal_core_memory(
        "policy",
        "路由层锁定规则",
        namespace=namespace,
        is_immutable=True,
    )

    from lantai.api.routes_memory import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: principal
    client = TestClient(app)
    response = client.put(
        "/core-memory",
        json={"block": "policy", "content": "路由越权改写", "namespace": namespace},
    )

    assert response.status_code == 403
    assert "sealed in Shishi vault and immutable" in response.json()["detail"]
    assert ShishiEngine(principal=principal).get_sealed_blocks(namespace=namespace)[0][
        "content"
    ] == ("路由层锁定规则")


def test_shishi_seal_route_returns_forbidden_for_immutable_block(param_env):
    principal = Principal(user_id="shishi-seal-route-owner", tenant_id="tenant-a")
    namespace = "shishi-seal-route-lock-test"
    engine = ShishiEngine(principal=principal)
    engine.seal_core_memory(
        "policy",
        "金匮路由锁定规则",
        namespace=namespace,
        is_immutable=True,
    )

    from lantai.api.routes_shishi import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: principal
    with TestClient(app) as client:
        response = client.post(
            "/shishi/seal",
            json={
                "block": "policy",
                "content": "从石室路由越权改写",
                "namespace": namespace,
            },
        )

    assert response.status_code == 403
    assert "sealed in Shishi vault and immutable" in response.json()["detail"]
    assert engine.get_sealed_blocks(namespace=namespace)[0]["content"] == "金匮路由锁定规则"


def test_put_core_memory_rechecks_lock_in_the_update_statement(param_env, monkeypatch):
    principal = Principal(user_id="shishi-race-owner", tenant_id="tenant-a")
    engine = ShishiEngine(principal=principal)
    namespace = "shishi-lock-race-test"
    engine.seal_core_memory(
        "policy",
        "可变的初始规则",
        namespace=namespace,
        is_immutable=False,
    )

    original_utcnow = memory_service.utcnow
    lock_added = False

    def add_lock_after_read():
        nonlocal lock_added
        if not lock_added:
            lock_added = True
            engine.seal_core_memory(
                "policy",
                "并发封存后的规则",
                namespace=namespace,
                is_immutable=True,
            )
        return original_utcnow()

    monkeypatch.setattr(memory_service, "utcnow", add_lock_after_read)
    with pytest.raises(PermissionError, match="sealed in Shishi vault and immutable"):
        put_core_memory(
            "policy",
            "读取后锁定仍被覆盖",
            namespace=namespace,
            principal=principal,
        )

    monkeypatch.setattr(memory_service, "utcnow", original_utcnow)
    report = engine.verify_integrity(namespace=namespace)
    blocks = engine.get_sealed_blocks(namespace=namespace)
    assert report["is_valid"] is True
    assert blocks[0]["content"] == "并发封存后的规则"
    assert blocks[0]["is_immutable"] is True


def test_put_core_memory_cannot_erase_a_concurrent_mutable_seal(param_env, monkeypatch):
    principal = Principal(user_id="shishi-seal-race-owner")
    namespace = "shishi-mutable-seal-race"
    put_core_memory("policy", "普通核心规则", namespace, principal=principal)
    engine = ShishiEngine(principal=principal)
    original_utcnow = memory_service.utcnow
    sealed = False

    def seal_after_read():
        nonlocal sealed
        if not sealed:
            sealed = True
            engine.seal_core_memory(
                "policy", "并发可变封存", namespace=namespace, is_immutable=False
            )
        return original_utcnow()

    monkeypatch.setattr(memory_service, "utcnow", seal_after_read)
    with pytest.raises(RuntimeError, match="changed during update; retry"):
        put_core_memory("policy", "旧写不应清空封存", namespace, principal=principal)

    assert engine.verify_integrity(namespace)["is_valid"] is True
    assert engine.get_sealed_blocks(namespace)[0]["content"] == "并发可变封存"


def test_put_core_memory_preserves_digest_for_mutable_shishi_block(param_env):
    session_factory, _ = param_env
    principal = Principal(user_id="shishi-mutable-owner", tenant_id="tenant-a")
    engine = ShishiEngine(principal=principal)
    namespace = "shishi-mutable-write-test"
    engine.seal_core_memory(
        "policy",
        "初始可变规则",
        namespace=namespace,
        is_immutable=False,
    )

    updated = put_core_memory(
        "policy",
        "经通用入口更新后的可变规则",
        namespace=namespace,
        principal=principal,
    )

    with session_factory() as session:
        row = session.exec(
            select(CoreMemoryBlock).where(
                CoreMemoryBlock.block == "policy",
                CoreMemoryBlock.namespace == namespace,
            )
        ).one()
    report = engine.verify_integrity(namespace=namespace)
    blocks = engine.get_sealed_blocks(namespace=namespace)
    assert updated["version"] == 2
    assert row.agent_id is not None and str(row.agent_id).startswith("sha256=")
    assert ":sealed" in str(row.agent_id)
    assert report["is_valid"] is True
    assert report["verified_blocks"] == 1
    assert blocks[0]["content"] == "经通用入口更新后的可变规则"


def test_integrity_reports_unsigned_core_blocks_as_unsealed(param_env):
    session_factory, _ = param_env
    namespace = "shishi-unsealed-test"
    with session_factory() as session:
        session.add(
            CoreMemoryBlock(
                id="cmb_shishi_unsealed_test",
                block="policy",
                content="历史核心规则尚未进入石室封存",
                namespace=namespace,
                user_id="shishi-auditor",
                agent_id="legacy-agent",
            )
        )
        session.commit()

    engine = ShishiEngine(principal=Principal(user_id="shishi-auditor", role="admin"))
    report = engine.verify_integrity(namespace=namespace)
    assert report["total_blocks"] == 1
    assert report["verified_blocks"] == 0
    assert report["unsealed_blocks"] == ["policy"]
    assert report["is_valid"] is False
    assert engine.get_sealed_blocks(namespace=namespace) == []


def test_get_sealed_blocks_never_returns_tampered_content(param_env):
    session_factory, _ = param_env
    namespace = "shishi-tampered-read-test"
    engine = ShishiEngine(principal=Principal(user_id="shishi-auditor", role="admin"))
    engine.seal_core_memory("policy", "原始封存规则", namespace=namespace)
    engine.seal_core_memory("task", "原始封存任务", namespace=namespace)

    with session_factory() as session:
        row = session.exec(
            select(CoreMemoryBlock).where(
                CoreMemoryBlock.block == "policy",
                CoreMemoryBlock.namespace == namespace,
            )
        ).one()
        row.content = "数据库直接篡改后的规则"
        session.add(row)
        session.commit()

    report = engine.verify_integrity(namespace=namespace)
    blocks = engine.get_sealed_blocks(namespace=namespace)
    assert report["is_valid"] is False
    assert report["tampered_blocks"] == ["policy"]
    assert report["verified_blocks"] == 1
    assert [block["block_key"] for block in blocks] == ["task"]


@pytest.mark.parametrize(
    "metadata",
    [
        "sha256=not-a-digest:sealed",
        "sha256=:sealed",
        "sha256=" + "A" * 64 + ":sealed",
        "prefix-sha256=" + "0" * 64 + ":sealed",
        "sha256=" + "0" * 64 + ":sealed:extra",
        "sha256=" + "0" * 64,
        "agent:sealed:immutable",
    ],
)
def test_integrity_rejects_malformed_sha256_metadata(param_env, metadata):
    session_factory, _ = param_env
    namespace = "shishi-malformed-digest-test"
    with session_factory() as session:
        session.add(
            CoreMemoryBlock(
                id="cmb_shishi_malformed_digest_test",
                block="policy",
                content="带无效摘要元数据的规则",
                namespace=namespace,
                user_id="shishi-auditor",
                agent_id=metadata,
            )
        )
        session.commit()

    engine = ShishiEngine(principal=Principal(user_id="shishi-auditor", role="admin"))
    report = engine.verify_integrity(namespace=namespace)
    assert report["is_valid"] is False
    assert report["verified_blocks"] == 0
    assert report["tampered_blocks"] == ["policy"]
    assert report["tamper_details"][0]["reason"] == "invalid_signature"
    assert engine.get_sealed_blocks(namespace=namespace) == []


def test_seal_core_memory_rechecks_immutable_lock_at_update_time(param_env, monkeypatch):
    principal = Principal(user_id="shishi-engine-race-owner", tenant_id="tenant-a")
    engine = ShishiEngine(principal=principal)
    admin_engine = ShishiEngine(principal=Principal(user_id="root", role="admin"))
    namespace = "shishi-engine-lock-race-test"
    engine.seal_core_memory(
        "policy",
        "可变规则",
        namespace=namespace,
        is_immutable=False,
    )

    original_utcnow = shishi_module.utcnow
    lock_added = False

    def add_lock_after_read():
        nonlocal lock_added
        if not lock_added:
            lock_added = True
            admin_engine.seal_core_memory(
                "policy",
                "管理员并发锁定的规则",
                namespace=namespace,
                is_immutable=True,
            )
        return original_utcnow()

    monkeypatch.setattr(shishi_module, "utcnow", add_lock_after_read)
    with pytest.raises(PermissionError, match="sealed in Shishi vault and immutable"):
        engine.seal_core_memory(
            "policy",
            "旧读快照试图解除锁",
            namespace=namespace,
            is_immutable=False,
        )

    monkeypatch.setattr(shishi_module, "utcnow", original_utcnow)
    report = engine.verify_integrity(namespace=namespace)
    blocks = engine.get_sealed_blocks(namespace=namespace)
    assert report["is_valid"] is True
    assert blocks[0]["content"] == "管理员并发锁定的规则"
    assert blocks[0]["is_immutable"] is True


def test_non_admin_cannot_reseal_a_tampered_mutable_block(param_env):
    session_factory, _ = param_env
    principal = Principal(user_id="shishi-tampered-owner")
    namespace = "shishi-reseal-tampered-test"
    engine = ShishiEngine(principal=principal)
    engine.seal_core_memory("policy", "原封存规则", namespace=namespace, is_immutable=False)
    with session_factory() as session:
        row = session.exec(
            select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
        ).one()
        row.content = "直接改库后的规则"
        session.add(row)
        session.commit()

    with pytest.raises(PermissionError, match="failed Shishi integrity verification"):
        engine.seal_core_memory("policy", "重写掩盖篡改", namespace=namespace)

    assert engine.verify_integrity(namespace)["tampered_blocks"] == ["policy"]


@pytest.mark.parametrize("operation", ["verify", "blocks"])
def test_integrity_reads_database_instead_of_stale_session_identity_map(param_env, operation):
    session_factory, _ = param_env
    principal = Principal(user_id="shishi-stale-session-owner")
    namespace = "shishi-stale-session-test"
    ShishiEngine(principal=principal).seal_core_memory("policy", "原始正文", namespace=namespace)
    with session_factory() as cached_session:
        cached_row = cached_session.exec(
            select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
        ).one()
        engine = ShishiEngine(cached_session, principal=principal)
        assert engine.verify_integrity(namespace)["is_valid"] is True
        with session_factory() as other_session:
            row = other_session.get(CoreMemoryBlock, cached_row.id)
            row.content = "另一个会话直接篡改"
            other_session.add(row)
            other_session.commit()

        if operation == "verify":
            assert engine.verify_integrity(namespace)["tampered_blocks"] == ["policy"]
        else:
            assert engine.get_sealed_blocks(namespace) == []


def test_shishi_read_scope_respects_tenant_with_same_user_id(param_env):
    namespace = "shishi-tenant-scope-test"
    owner = Principal(user_id="same-user", tenant_id="tenant-a")
    other = Principal(user_id="same-user", tenant_id="tenant-b")
    ShishiEngine(principal=owner).seal_core_memory("policy", "租户甲规则", namespace=namespace)

    engine = ShishiEngine(principal=other)
    assert engine.get_sealed_blocks(namespace) == []
    assert engine.verify_integrity(namespace)["total_blocks"] == 0


def test_generic_core_memory_entry_cannot_cross_tenant_for_mutable_seal(param_env):
    namespace = "shishi-put-tenant-test"
    owner = Principal(user_id="same-put-user", tenant_id="tenant-a")
    other = Principal(user_id="same-put-user", tenant_id="tenant-b")
    engine = ShishiEngine(principal=owner)
    engine.seal_core_memory("policy", "租户甲可变规则", namespace=namespace, is_immutable=False)

    with pytest.raises(PermissionError, match="another tenant"):
        put_core_memory("policy", "租户乙越权覆盖", namespace, principal=other)

    assert memory_service.get_core_memory(namespace, principal=other)["blocks"] == []
    assert engine.get_sealed_blocks(namespace)[0]["content"] == "租户甲可变规则"


@pytest.mark.parametrize("entry", ["put", "seal"])
@pytest.mark.parametrize("immutable", [True, False])
def test_first_write_collision_respects_concurrent_seal(param_env, monkeypatch, entry, immutable):
    principal = Principal(user_id="shishi-first-write-owner")
    namespace = "shishi-first-write-collision"
    engine = ShishiEngine(principal=principal)
    inserted = False
    module = memory_service if entry == "put" else shishi_module
    name = "new_id" if entry == "put" else "ULID"
    original = getattr(module, name)

    def insert_seal_before_first_write(*args, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            engine.seal_core_memory(
                "policy", "先落库的封存", namespace=namespace, is_immutable=immutable
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, insert_seal_before_first_write)

    def write():
        if entry == "put":
            return put_core_memory("policy", "后到的写入", namespace, principal=principal)
        return engine.seal_core_memory(
            "policy", "后到的写入", namespace=namespace, is_immutable=False
        )

    if immutable:
        with pytest.raises(PermissionError, match="sealed in Shishi vault and immutable"):
            write()
    else:
        assert write()["version"] == 2

    blocks = engine.get_sealed_blocks(namespace)
    assert len(blocks) == 1
    assert blocks[0]["content"] == ("先落库的封存" if immutable else "后到的写入")
    assert blocks[0]["is_immutable"] is immutable
    assert engine.verify_integrity(namespace)["is_valid"] is True


def test_administrator_can_explicitly_repair_seal_without_changing_owner(param_env):
    session_factory, _ = param_env
    owner = Principal(user_id="shishi-repair-owner", tenant_id="tenant-a")
    namespace = "shishi-admin-repair-test"
    owner_engine = ShishiEngine(principal=owner)
    owner_engine.seal_core_memory("policy", "原规则", namespace=namespace)
    with session_factory() as session:
        row = session.exec(
            select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
        ).one()
        row.content = "被篡改的规则"
        session.add(row)
        session.commit()

    admin = Principal(user_id="repair-admin", role="admin")
    repaired = ShishiEngine(principal=admin).seal_core_memory(
        "policy", "管理员裁决恢复的规则", namespace=namespace, is_immutable=True
    )

    assert repaired["user_id"] == owner.user_id
    assert repaired["tenant_id"] == owner.tenant_id
    assert repaired["version"] == 2
    assert owner_engine.verify_integrity(namespace)["is_valid"] is True


def test_core_memory_route_reports_seal_race_as_conflict(param_env, monkeypatch):
    principal = Principal(user_id="shishi-conflict-route-owner")
    namespace = "shishi-conflict-route-test"
    put_core_memory("policy", "初始普通规则", namespace, principal=principal)
    original_utcnow = memory_service.utcnow
    sealed = False

    def seal_after_read():
        nonlocal sealed
        if not sealed:
            sealed = True
            ShishiEngine(principal=principal).seal_core_memory(
                "policy", "并发封存规则", namespace=namespace, is_immutable=False
            )
        return original_utcnow()

    from lantai.api.routes_memory import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: principal
    monkeypatch.setattr(memory_service, "utcnow", seal_after_read)
    with TestClient(app) as client:
        response = client.put(
            "/core-memory",
            json={"block": "policy", "content": "过期写入", "namespace": namespace},
        )

    assert response.status_code == 409
    assert "changed during update; retry" in response.json()["detail"]
    assert ShishiEngine(principal=principal).verify_integrity(namespace)["is_valid"] is True


def test_unsealed_concurrent_writers_keep_existing_last_write_wins_contract(param_env, monkeypatch):
    namespace = "shishi-unsealed-last-write-test"
    first = Principal(user_id="shishi-concurrent-user", agent_id="agent-a")
    second = Principal(user_id="shishi-concurrent-user", agent_id="agent-b")
    put_core_memory("task", "初始任务", namespace, principal=first)
    original_utcnow = memory_service.utcnow
    interleaved = False

    def other_writer_after_read():
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            put_core_memory("task", "先完成的更新", namespace, principal=second)
        return original_utcnow()

    monkeypatch.setattr(memory_service, "utcnow", other_writer_after_read)
    result = put_core_memory("task", "最后完成的更新", namespace, principal=first)

    assert result["version"] == 3
    assert result["content"] == "最后完成的更新"
    assert result["agent_id"] == "agent-a"


def test_seal_cannot_write_after_administrator_transfers_ownership(param_env, monkeypatch):
    owner = Principal(user_id="shishi-owner-before-transfer", tenant_id="tenant-a")
    admin = Principal(user_id="shishi-owner-after-transfer", tenant_id="tenant-b", role="admin")
    namespace = "shishi-ownership-race-test"
    engine = ShishiEngine(principal=owner)
    engine.seal_core_memory("policy", "待转移规则", namespace=namespace, is_immutable=False)
    original_utcnow = shishi_module.utcnow
    transferred = False

    def transfer_after_read():
        nonlocal transferred
        if not transferred:
            transferred = True
            put_core_memory("policy", "待转移规则", namespace, principal=admin)
        return original_utcnow()

    monkeypatch.setattr(shishi_module, "utcnow", transfer_after_read)
    with pytest.raises(RuntimeError, match="changed during seal; retry"):
        engine.seal_core_memory("policy", "旧属主不得继续写", namespace=namespace)

    assert engine.get_sealed_blocks(namespace) == []
    blocks = ShishiEngine(principal=admin).get_sealed_blocks(namespace)
    assert blocks[0]["content"] == "待转移规则"
    assert blocks[0]["version"] == 2
