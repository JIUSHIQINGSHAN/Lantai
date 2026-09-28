"""记忆分类树归属：A 不再看到 B 的节点描述，也数不出 B 在某节点下挂了多少条。

票 `.scratch/readside-gaps/issues/08-tree-edges-readside-no-identity.md`

**先说影响**：`GET /tree` 与 `/tree/subtree` 一个身份都不取，返回**整棵树**
的节点——`description` 是自由文本（第五轮实证 A 打过去 len=189 含 B 的密文），
并且每个节点带**挂载计数**。计数比描述更隐蔽：节点名收窄了它照样漏，
**A 能数出 B 在某个节点下挂了多少条记忆**。

`MemoryNode` 此前**一个归属列都没有** → 加列 + 迁移（v26）。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, MemoryNode


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def tree_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
    ):
        yield session_factory


def _node(sf, path: str, user_id: str | None, description: str, **kw) -> None:
    fields = dict(
        name=path.rsplit("/", 1)[-1] or "root",
        node_path=path,
        depth=len([s for s in path.split("/") if s]),
        description=description,
        namespace="default",
        created_at=utcnow(),
    )
    fields.update(kw)
    with sf() as s:
        s.add(MemoryNode(id=f"node-{path}", parent_id=None, user_id=user_id, **fields))
        s.commit()


def _mem(sf, mem_id: str, user_id: str | None, tree_path: str | None) -> None:
    with sf() as s:
        s.add(
            MemoryItem(
                id=mem_id,
                user_id=user_id,
                title=mem_id,
                content=f"{mem_id} 的正文",
                lane="fact",
                status="active",
                importance=0.5,
                decay_score=0.9,
                tier="working",
                use_count=0,
                helpful_count=0,
                tree_path=tree_path,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()


def _paths(result: dict) -> list[str]:
    return [n["node_path"] for n in result["nodes"]]


def _descriptions(result: dict) -> list[str]:
    return [n["description"] for n in result["nodes"]]


def _counts(result: dict) -> dict[str, dict]:
    return {n["node_path"]: n["attachments"] for n in result["nodes"]}


# ── Red 1 / 2：读侧收窄（节点 + 计数）──────────────────────


class TestTreeReadScope:
    def test_get_subtree_excludes_other_users_nodes(self, tree_env):
        """**Red 1（决定性）**：A `get_subtree` → 不含 B 的节点。

        断言落在 `node_path` 与 `description` 上，不只「机密不在响应里」——
        后者会因为节点本来就叫别的名字而空过。
        """
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/", principal=_principal("user-A"))
        assert "/secret" not in _paths(res), f"B 的节点路径漏出来了：{_paths(res)}"
        assert "B 的机密节点描述" not in _descriptions(res), (
            f"B 的节点描述漏出来了：{_descriptions(res)}"
        )

    def test_attachment_counts_exclude_other_users(self, tree_env):
        """**Red 2**：B 的挂载计数不出现——否则节点名收窄了计数照样漏。"""
        sf = tree_env
        _node(sf, "/proj", None, "共享项目节点")
        # B 在这个节点下挂 3 条
        for i in range(3):
            _mem(sf, f"mB-{i}", "user-B", "/proj")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/", principal=_principal("user-A"))
        counts = _counts(res)
        assert counts.get("/proj", {}).get("subtree") == 0, (
            f"A 数出了 B 挂在 /proj 下的记忆条数：{counts.get('/proj')}"
        )

    def test_own_attachments_still_counted(self, tree_env):
        """Red 6（反向）：A 自己挂的记忆照常计数（收窄不是把功能修废）。"""
        sf = tree_env
        _node(sf, "/proj", None, "共享项目节点")
        _mem(sf, "mA-1", "user-A", "/proj")
        _mem(sf, "mA-2", "user-A", "/proj")
        _mem(sf, "mB-1", "user-B", "/proj")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/", principal=_principal("user-A"))
        counts = _counts(res)
        assert counts.get("/proj", {}).get("direct") == 2, (
            f"A 自己的挂载计数不对：{counts.get('/proj')}（应为 2）"
        )

    def test_null_owner_nodes_still_visible(self, tree_env):
        """Red 4：NULL 属主的老节点对非 admin 仍可见（树不能整棵消失）。"""
        sf = tree_env
        _node(sf, "/legacy", None, "老节点描述")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/", principal=_principal("user-A"))
        assert "/legacy" in _paths(res), f"NULL 属主老节点被漏掉了：{_paths(res)}"

    def test_admin_sees_all_nodes(self, tree_env):
        """Red 5：admin 照见全部。"""
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/", principal=_principal(None, role="admin"))
        assert "/secret" in _paths(res), f"admin 下 B 的节点不见了：{_paths(res)}"

    def test_internal_call_unfiltered(self, tree_env):
        """`principal=None`（内部/脚本）→ 全表，行为不变。"""
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")

        from lantai.services.tree_service import get_subtree

        with sf() as s:
            res = get_subtree(s, "/")
        assert "/secret" in _paths(res), f"内部调用被收窄了：{_paths(res)}"

    def test_view_tree_wrapper_threads_principal(self, tree_env):
        """`view_tree` 包装也要透传 principal（它被 /tree 路由直接调用）。"""
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")
        _node(sf, "/mine", "user-A", "A 的节点")

        from lantai.services.tree_service import view_tree

        res = view_tree(principal=_principal("user-A"))
        paths = _paths(res)
        assert "/secret" not in paths, f"经 view_tree 漏出 B 的节点：{paths}"
        assert "/mine" in paths, f"A 自己的节点被漏掉了：{paths}"


# ── Red 3：写侧归属 ──────────────────────────────────────────


class TestTreeNodeWriteScope:
    def test_add_node_records_owner(self, tree_env):
        """**Red 3**：A `POST /tree/nodes` 新建 → 落库行 `user_id` 是 A。"""
        sf = tree_env

        from lantai.services.tree_service import add_node

        with sf() as s:
            out = add_node(s, "mine", "/", "我的节点", principal=_principal("user-A"))
        node_id = out["node"]["id"]
        with sf() as s:
            row = s.get(MemoryNode, node_id)
        assert row.user_id == "user-A", f"新节点没落属主：user_id={row.user_id!r}"

    def test_route_requires_identity(self, tree_env):
        """路由层带身份时新建节点落 A（Depends(get_current_user) 真接上了）。"""
        sf = tree_env
        from lantai.api.app import app
        from lantai.models.schemas import TreeAddNodeReq  # noqa: F401

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            from fastapi.testclient import TestClient

            with TestClient(app) as c:
                r = c.post("/tree/nodes", json={"name": "viahttp", "parent_path": "/"})
            assert r.status_code == 200, f"新建节点失败：{r.status_code} {r.text[:200]}"
            node_id = r.json()["node"]["id"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        with sf() as s:
            row = s.get(MemoryNode, node_id)
        assert row.user_id == "user-A", f"经路由新建的节点没落属主：user_id={row.user_id!r}"

    def test_owner_can_still_assign_own_memory(self, tree_env):
        """Red 6（写侧）：owner 对自己的记忆照旧能挂到节点。"""
        sf = tree_env
        _node(sf, "/proj", None, "共享项目节点")
        _mem(sf, "mA-1", "user-A", None)

        from lantai.services.tree_service import assign_memory

        with sf() as s:
            res = assign_memory(s, "mA-1", "/proj", principal=_principal("user-A"))
        assert res.get("ok") is True, f"A 挂自己的记忆被拒：{res}"
        with sf() as s:
            assert s.get(MemoryItem, "mA-1").tree_path == "/proj"


# ── 路由层（读侧两个端点）────────────────────────────────────


class TestTreeRouteReadScope:
    """Red 1/2 的路由层复现：`/tree` 与 `/tree/subtree` 是真实的 HTTP 出口。

    直接调 service 只证明 service 修好了；宿主打的是 HTTP，路由少取一次
    身份、少传一次 principal，泄漏照样发生（票 12/13 都在路由层栽过）。
    """

    def _client(self, principal):
        from lantai.api.app import app

        app.dependency_overrides[get_current_user] = lambda: principal
        return TestClient(app)

    def test_route_tree_excludes_other_users_nodes(self, tree_env):
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")
        _node(sf, "/mine", "user-A", "A 的节点")

        app_over = self._client(_principal("user-A"))
        try:
            with app_over as c:
                r = c.get("/tree")
            assert r.status_code == 200, f"/tree 失败：{r.status_code} {r.text[:200]}"
            body = r.json()
        finally:
            from lantai.api.app import app

            app.dependency_overrides.pop(get_current_user, None)

        paths = [n["node_path"] for n in body["nodes"]]
        descs = [n["description"] for n in body["nodes"]]
        assert "/secret" not in paths, f"经 /tree 漏出 B 的节点：{paths}"
        assert "B 的机密节点描述" not in descs, f"经 /tree 漏出 B 的节点描述：{descs}"
        assert "/mine" in paths, f"A 自己的节点不见了（被误收窄？）：{paths}"

    def test_route_subtree_excludes_other_users_counts(self, tree_env):
        """计数也要在路由层收窄——否则节点名收窄了、A 仍能数出 B 挂了多少。"""
        sf = tree_env
        _node(sf, "/proj", None, "共享项目节点")
        for i in range(3):
            _mem(sf, f"mB-{i}", "user-B", "/proj")
        _mem(sf, "mA-1", "user-A", "/proj")

        app_over = self._client(_principal("user-A"))
        try:
            with app_over as c:
                r = c.get("/tree/subtree", params={"path": "/"})
            assert r.status_code == 200, f"/tree/subtree 失败：{r.status_code} {r.text[:200]}"
            body = r.json()
        finally:
            from lantai.api.app import app

            app.dependency_overrides.pop(get_current_user, None)

        counts = {n["node_path"]: n["attachments"] for n in body["nodes"]}
        assert counts.get("/proj", {}).get("direct") == 1, (
            f"经 /tree/subtree 数出了别人的挂载：{counts.get('/proj')}（应为 1，只有 A 自己那条）"
        )

    def test_route_tree_admin_sees_all(self, tree_env):
        sf = tree_env
        _node(sf, "/secret", "user-B", "B 的机密节点描述")

        app_over = self._client(_principal(None, role="admin"))
        try:
            with app_over as c:
                r = c.get("/tree")
            assert r.status_code == 200, f"/tree 失败：{r.status_code}"
            body = r.json()
        finally:
            from lantai.api.app import app

            app.dependency_overrides.pop(get_current_user, None)

        assert "/secret" in [n["node_path"] for n in body["nodes"]], "admin 下 B 的节点不见了"


# ── 迁移 ─────────────────────────────────────────────────────


class TestTreeNodeMigration:
    def test_migration_adds_ownership_columns_idempotent(self, tree_env):
        """v26 迁移：memorynode 补 tenant_id / user_id / agent_id，幂等。"""
        sf = tree_env
        from lantai.storage.db import apply_migrations

        # fixture 里 create_all 已建表；apply_migrations 走的是 driver connection
        with sf() as s:
            raw = s.connection().connection.driver_connection
        apply_migrations(raw)
        apply_migrations(raw)  # 第二遍不重复加列、不报错

        cols = {c.name for c in MemoryNode.__table__.columns}
        for col in ("tenant_id", "user_id", "agent_id"):
            assert col in cols, f"MemoryNode 缺归属列 {col}"
        # 跑完迁移 session 仍可用（迁移没把连接弄坏）
        with sf() as s:
            assert s.get(MemoryNode, "node-/nonexistent") is None
