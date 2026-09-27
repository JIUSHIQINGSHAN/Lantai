"""分类树挂载归属校验：票 .scratch/ownership-gaps/05

`POST /tree/assign`、`/tree/unassign`、`/work-items/batch/organize` 三个
端点都不取身份，`tree_service.assign_memory` 拿到 memory_id 就改
`mem.tree_path`——不查这条记忆是谁的。实测：A 能把 B 的记忆挂到自己的
节点、也能把它摘下来，批量接口一次一大堆。

判据沿用 `acl.ensure_can_delete` 这一个真源（票 01/04 的教训：同一判据
散落多处必然漏），语义取「改写他人记忆的元数据 = 破坏性操作」，与
`PUT/DELETE /terminal/memory/{id}` 同口径。

测试策略：真实临时 SQLite + 真实 FastAPI app，不 mock 内部计算。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user


def _principal(user_id: str, *, admin: bool = False, lanes=("fact",)):
    return Principal(
        user_id=user_id,
        allowed_lanes=list(lanes),
        role="admin" if admin else "user",
    )


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def tree_env(param_env):
    """param_env 底座：内存 SQLite + 真实建表（含 FTS）。"""
    return param_env


def _seed(session_factory, *, memory_id="memB", owner="user-B", lane="fact"):
    from lantai.models.tables import MemoryItem

    with session_factory() as s:
        s.add(
            MemoryItem(
                id=memory_id,
                memory_type="semantic",
                key=f"k-{memory_id}",
                content="B 的记忆内容",
                lane=lane,
                status="active",
                importance=0.5,
                user_id=owner,
            )
        )
        s.commit()


# ── Red 1/2（决定性）：A 不能动 B 的记忆挂载 ────────────────────


class TestTreeAssignOwnership:
    def test_assign_other_users_memory_is_rejected(self, tree_env):
        """A 把 B 的记忆挂到节点 → 403，且 tree_path 不许被改。"""
        session_factory, _ = tree_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            _as(_principal("user-A"), lambda: c.post("/tree/nodes", json={"name": "audit-node"}))
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/tree/assign", json={"memory_id": "memB", "node_path": "/audit-node"}
                ),
            )

        assert resp.status_code == 403, (
            f"A 改写了 B 的记忆挂载（{resp.status_code} {resp.text}）——"
            "assign 不查归属，等于谁能建节点谁就能归集全库记忆"
        )
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            m = s.get(MemoryItem, "memB")
            assert m.tree_path is None, f"拒绝了但 tree_path 仍被改成 {m.tree_path!r}"

    def test_unassign_other_users_memory_is_rejected(self, tree_env):
        """A 摘除 B 的记忆挂载 → 403。"""
        session_factory, _ = tree_env
        _seed(session_factory)
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            m = s.get(MemoryItem, "memB")
            m.tree_path = "/somewhere"
            s.add(m)
            s.commit()

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-A"), lambda: c.post("/tree/unassign", json={"memory_id": "memB"})
            )

        assert resp.status_code == 403, f"A 摘除了 B 的记忆挂载（{resp.status_code} {resp.text}）"
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            assert s.get(MemoryItem, "memB").tree_path == "/somewhere", "拒绝了但仍被改写"


# ── Red 3：批量接口同口径 ──────────────────────────────────────


class TestBatchOrganizeOwnership:
    def test_batch_organize_other_users_memory_fails_per_item(self, tree_env):
        """批量挂载他人记忆：该条进 failed、整体 ok=False，不许静默成功。"""
        session_factory, _ = tree_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            _as(_principal("user-A"), lambda: c.post("/tree/nodes", json={"name": "audit-node"}))
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/work-items/batch/organize",
                    json={"memory_ids": ["memB"], "node_path": "/audit-node"},
                ),
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is False, f"批量改写了他人记忆却报 ok（{data}）"
        assert [f["source_id"] for f in data["failed"]] == ["memB"], (
            f"失败条目没如实记录：{data}"
        )
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            assert s.get(MemoryItem, "memB").tree_path is None, "失败了但 tree_path 仍被改"


# ── Red 4/5：不能把功能修废 ────────────────────────────────────


class TestNoRegression:
    def test_owner_can_assign_own_memory(self, tree_env):
        """A assign 自己的记忆 → 200 且真的挂上。"""
        session_factory, _ = tree_env
        _seed(session_factory, memory_id="memA", owner="user-A")
        with TestClient(_app()) as c:
            _as(_principal("user-A"), lambda: c.post("/tree/nodes", json={"name": "mine"}))
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/tree/assign", json={"memory_id": "memA", "node_path": "/mine"}),
            )
        assert resp.status_code == 200, resp.text
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            assert s.get(MemoryItem, "memA").tree_path == "/mine"

    def test_admin_can_assign_any_memory(self, tree_env):
        """admin 全权（与 ensure_can_delete 同口径）：运维整理分类不被挡住。"""
        session_factory, _ = tree_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            _as(_principal("ops", admin=True), lambda: c.post("/tree/nodes", json={"name": "ops"}))
            resp = _as(
                _principal("ops", admin=True),
                lambda: c.post("/tree/assign", json={"memory_id": "memB", "node_path": "/ops"}),
            )
        assert resp.status_code == 200, resp.text

    def test_service_layer_rejects_cross_user_assign(self, tree_env):
        """service 层直调也要拦（MCP/内部调用不走 HTTP，同样会漏）。"""
        from lantai.services.tree_service import assign_memory

        session_factory, _ = tree_env
        _seed(session_factory)
        with session_factory() as s:
            from lantai.models.tables import MemoryNode

            s.add(
                MemoryNode(
                    id="node_svc",
                    name="svc",
                    node_path="/svc",
                    depth=1,
                    namespace="default",
                )
            )
            s.commit()
        with session_factory() as s:
            with pytest.raises(Exception) as exc:
                assign_memory(s, "memB", "/svc", principal=_principal("user-A"))
        assert "403" in str(exc.value) or "another user" in str(exc.value), str(exc.value)


def _app():
    from lantai.api.app import app

    return app
