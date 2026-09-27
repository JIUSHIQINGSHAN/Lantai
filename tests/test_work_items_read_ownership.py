"""案牍读侧归属：票 .scratch/readside-gaps/01

`GET /work-items` 与 `GET /work-items/detail/*` 一个身份都不取，
`load_work_item_snapshot()` 全表 `select(MemoryItem)`。后果比票
ownership-gaps/01（`GET /memories` 漏 160 字符摘要）更重——案牍的
memory 分支 `summary` 放的是 `content` **全文**（[:240]），`title` 放的是
`MemoryItem.key`，外加 `source_id`（记忆 ULID）。

而 id 本身就是攻击材料：`POST /edges`、`/tree/assign`、
`/terminal/memory/{id}` 全都要 id。`OBSERVED.txt` 段 D 已实证「A read
B's memory content AND its id (needed to mount attack A)」。

同票的 candidate / proposal 分支同理（见票 02），本票只覆盖 memory 分支
与 detail 的越权返回。

测试策略同 ownership-gaps 系列：真实临时 SQLite + 真实产品路径，不 mock
内部计算；断言落在**行为**上——换个 user 读，读到的是别人的项不存在，
而不是"读到了但带了个归属标记"（票 01 的教训）。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


def _principal(user_id: str, *, role: str = "user", tenant_id: str | None = None):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        allowed_lanes=["general", "fact", "preference", "policy"],
        role=role,
    )


def _as(principal, fn):
    """以指定身份执行一次 HTTP 调用。"""
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _app():
    from lantai.api.app import app

    return app


def _seed_memory(session_factory, *, memory_id: str, user_id: str, content: str, tree_path=None):
    """真实构造一条 MemoryItem（不 mock，直插临时库）。"""
    now = utcnow()
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=memory_id,
                memory_type="text",
                namespace="default",
                key=f"key-{user_id}",
                content=content,
                lane="general",
                domain="user",
                tier="working",
                decay_class="semantic",
                status="active",
                version=1,
                tree_path=tree_path,
                user_id=user_id,
                tenant_id="tenant-1",
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


# ── Red 1（决定性）：A 的案牍列表不含 B 的记忆全文与 id ──────────


class TestWorkItemsReadOwnership:
    def test_other_users_memory_absent_from_work_items(self, param_env):
        """跨用户列案牍：B 的记忆正文/key/id 一样都不能出现。

        现状：全表捞，`summary=content[:240]`，连 key 和 source_id 一起给。
        """
        session_factory, _ = param_env
        _seed_memory(
            session_factory, memory_id="mem-A", user_id="user-A", content="A 的连接池上限是 500"
        )
        _seed_memory(
            session_factory,
            memory_id="mem-B",
            user_id="user-B",
            content="B 的机密：连接池上限 100，数据库密码 hunter2",
        )

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/work-items?limit=100"))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body["items"]
        memory_items = [i for i in items if i["kind"] == "memory"]
        assert memory_items, "A 自己的未挂载记忆应该出现在 organization_needed（不能修废功能）"
        for item in memory_items:
            assert item["source_id"] == "mem-A", (
                f"A 的案牍里出现了别人的记忆 id：{item['source_id']}"
            )
        # 全文/key/id 一样都不该漏
        assert "hunter2" not in resp.text, "B 的记忆正文漏到了 A 的案牍响应里"
        assert "mem-B" not in resp.text, "B 的记忆 id 漏到了 A 的案牍响应里（id 是攻击材料）"
        assert "key-user-B" not in resp.text, "B 的记忆 key 漏到了 A 的案牍响应里"
        assert body["total"] == len(memory_items)

    def test_work_items_detail_of_other_user_is_404(self, param_env):
        """A 取 B 的记忆详情必须 404——不是 403。

        403 会告诉 A「这条存在但不是你的」，本身就是信息泄漏；同札记口径。
        现状：200 + 46 个字段的 model_dump 全文。
        """
        session_factory, _ = param_env
        _seed_memory(
            session_factory,
            memory_id="mem-B",
            user_id="user-B",
            content="B 的机密：连接池上限 100，数据库密码 hunter2",
        )

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/work-items/detail/memory/mem-B"),
            )

        assert resp.status_code == 404, (
            f"A 读到了 B 的记忆详情：{resp.status_code} {resp.text[:200]}"
        )
        assert "hunter2" not in resp.text

    def test_owner_still_sees_own_memory_detail(self, param_env):
        """B 自己取详情照旧 200——不能把功能修废。"""
        session_factory, _ = param_env
        _seed_memory(session_factory, memory_id="mem-B", user_id="user-B", content="B 自己的内容")

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.get("/work-items/detail/memory/mem-B"),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["source"]["content"] == "B 自己的内容"

    def test_admin_still_sees_all_memories(self, param_env):
        """admin 照见全部——不能把运维控制台修废。"""
        session_factory, _ = param_env
        _seed_memory(session_factory, memory_id="mem-A", user_id="user-A", content="A 的内容")
        _seed_memory(session_factory, memory_id="mem-B", user_id="user-B", content="B 的内容")

        with TestClient(_app()) as c:
            resp = _as(_principal("admin-1", role="admin"), lambda: c.get("/work-items?limit=100"))

        assert resp.status_code == 200, resp.text
        ids = {i["source_id"] for i in resp.json()["items"] if i["kind"] == "memory"}
        assert ids == {"mem-A", "mem-B"}, f"admin 看不到全部：{ids}"

    def test_detail_tree_gated_to_admin(self, param_env):
        """detail 里的整树视图只给 admin：普通调用方拿 None。

        `related["tree"]` 是 `get_subtree(s, "/")`——整树，含**他人**的
        节点名与挂载计数（票 readside-gaps/03 的结构泄漏）。本票先按
        「不放宽也不静默给别人的树」处理：admin 拿树，其他人拿 None。
        """
        session_factory, _ = param_env
        _seed_memory(session_factory, memory_id="mem-B", user_id="user-B", content="B 自己的内容")

        with TestClient(_app()) as c:
            owner_resp = _as(_principal("user-B"), lambda: c.get("/work-items/detail/memory/mem-B"))
            admin_resp = _as(
                _principal("admin-1", role="admin"),
                lambda: c.get("/work-items/detail/memory/mem-B"),
            )

        assert owner_resp.status_code == 200, owner_resp.text
        assert owner_resp.json()["related"]["tree"] is None, (
            "非 admin 的 detail 拿到了整树视图（含他人节点名与挂载计数）"
        )
        assert admin_resp.status_code == 200, admin_resp.text
        assert admin_resp.json()["related"]["tree"] is not None, (
            "admin 的 detail 反而没有整树视图——把运维控制台修废了"
        )

    def test_internal_call_without_principal_converges_to_default(self, param_env):
        """principal=None（内部调用/MCP）按 "default" 收敛，不报错也不漏别人的。

        口径同 memory_service.get_core_memory：NULL 是「未记录」的事实状态，
        不是「属于所有人」。
        """
        session_factory, _ = param_env
        _seed_memory(
            session_factory, memory_id="mem-def", user_id="default", content="未记录归属的内容"
        )
        _seed_memory(
            session_factory, memory_id="mem-B", user_id="user-B", content="B 的机密 hunter2"
        )

        from lantai.services.work_item_service import list_work_items

        result = list_work_items(limit=100)
        ids = {i.source_id for i in result.items if i.kind == "memory"}
        assert "mem-def" in ids, "default 归属的记忆该被内部调用看见"
        assert "mem-B" not in ids, f"内部调用漏了别人的记忆：{ids}"

    def test_null_owner_legacy_row_not_treated_as_everyones(self, param_env):
        """NULL 属主的老行（迁移前/脚本直插）不该被任意 user 看见。

        这条锁的是「NULL ≠ 属于所有人」这个判读。库里现有 615 行 NULL 属主
        的 memoryitem（真实库实测），判错就是把全库老数据敞开。
        """
        session_factory, _ = param_env
        _seed_memory(
            session_factory, memory_id="mem-legacy", user_id=None, content="老行：未记录归属"
        )

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/work-items?limit=100"))

        assert resp.status_code == 200, resp.text
        assert "mem-legacy" not in resp.text, "NULL 属主的老行被任意登录用户看见了"
