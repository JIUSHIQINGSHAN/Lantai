"""定点写入口归属：rollback / feedback 不再跨用户改写单条记忆。

票 `.scratch/readside-gaps/13-rollback-feedback-cross-user.md`

**先说影响**：票 12 修完三个「全库演化」入口后，第八轮改查**按 id 定点**
的写入口——它们不扫全表，所以票 12 的候选集收窄对它们完全无效。两个中：
`rollback` 能把别人的正文整条覆盖成历史任意版本（无 undo），`feedback`
能刷别人的 `use_count` / `importance`。

**三条入口同一处代码**：REST 路由、MCP 工具、service 层（还被 worker 消费）。
只修 REST 会留下「以为修完了」的错觉，故三处一并修。

**口径**：`acl.ensure_can_delete` 单一真源（同票 04/06/11 写侧范式）——
admin/system 全权；资源标了 user_id 且与主体不同 → 拒绝；资源 user_id
为空（历史行）→ 不视为越权。`principal=None`（worker/CLI/scheduler）
保持全表，与已修各票逐字一致。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCheckpoint, MemoryItem


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def pw_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # rollback 路径会 sync_fts（同事务强一致）——create_all 不建 FTS5 虚表
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.llm.client.chat_json", return_value={}),
    ):
        yield session_factory


def _add(sf, mem_id: str, user_id: str | None, **kw) -> None:
    fields = dict(
        title=mem_id,
        content=f"{mem_id} 的当前正文",
        lane="fact",
        status="active",
        importance=0.9,
        decay_score=0.9,
        tier="working",
        use_count=0,
        helpful_count=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    fields.update(kw)
    with sf() as s:
        s.add(MemoryItem(id=mem_id, user_id=user_id, **fields))
        s.commit()


def _checkpoints(sf, mem_id: str, n: int, content: str) -> None:
    """n 个 checkpoint：rollback 需要 >=2 才有"上一版"。"""
    for i in range(n):
        with sf() as s:
            s.add(
                MemoryCheckpoint(
                    id=f"cp_{mem_id}_{i}",
                    memory_id=mem_id,
                    version=i + 1,
                    before={"content": "旧正文"},
                    after={"content": content},
                    trigger="update",
                    created_at=utcnow(),
                )
            )
            s.commit()


def _row(sf, mem_id: str):
    with sf() as s:
        return s.get(MemoryItem, mem_id)


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── rollback ────────────────────────────────────────────────────


class TestRollbackWriteOwnership:
    def test_other_users_memory_not_rolled_back(self, pw_env):
        """**Red 1（决定性）**：A 回滚 B 的记忆，B 的 content 逐字节不变。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")
        _checkpoints(sf, "mem-B", 3, "B 的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback("mem-B", principal=_principal("user-A"))

        row = _row(sf, "mem-B")
        assert row is not None, "B 的记忆没了"
        assert row.content == "mem-B 的当前正文", (
            f"A 回滚改写了 B 的正文：现为 {row.content!r}（写侧越权）"
        )
        assert res.get("ok") is False, f"越权回滚竟然返回 ok：{res}"

    def test_own_memory_still_rolled_back(self, pw_env):
        """Red 3：A 回滚自己的记忆照常生效（收窄不是把功能修废）。"""
        sf = pw_env
        _add(sf, "mem-A", "user-A")
        _checkpoints(sf, "mem-A", 3, "A 的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback("mem-A", principal=_principal("user-A"))

        row = _row(sf, "mem-A")
        assert res.get("ok") is True, f"A 回滚自己的记忆失败了：{res}"
        assert row.content == "A 的历史正文", f"回滚没生效：{row.content}"

    def test_null_owner_memory_still_rolled_back(self, pw_env):
        """Red 4：NULL 属主历史行仍可操作（单人部署下不能锁死老数据）。"""
        sf = pw_env
        _add(sf, "mem-null", None)
        _checkpoints(sf, "mem-null", 3, "null 属主的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback("mem-null", principal=_principal("user-A"))

        assert res.get("ok") is True, f"NULL 属主历史行被锁死了：{res}"
        assert _row(sf, "mem-null").content == "null 属主的历史正文"

    def test_admin_rolls_back_anything(self, pw_env):
        """Red 5：admin 全权（与改动前逐字一致）。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")
        _checkpoints(sf, "mem-B", 3, "B 的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback("mem-B", principal=_principal(None, role="admin"))

        assert res.get("ok") is True, f"admin 没能回滚：{res}"
        assert _row(sf, "mem-B").content == "B 的历史正文"

    def test_internal_call_unfiltered(self, pw_env):
        """Red 6：principal=None 内部调用行为不变（worker 仍能跑）。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")
        _checkpoints(sf, "mem-B", 3, "B 的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback("mem-B")

        assert res.get("ok") is True, f"内部调用被收窄了：{res}"
        assert _row(sf, "mem-B").content == "B 的历史正文"

    def test_route_returns_403_not_200_ok_false(self, pw_env):
        """**Red 7（路由层）**：越权回滚经 REST 得 **403**，不是 200 + ok:false。

        200 + ok:false 语义上成功、实际失败，调用方必须翻 body 才知道出错
        （票 api-error-status/01 治的正是这个）。而落进 422 也不对——422 是
        「请求格式有问题」，会让调用方以为自己的 body 写错了，实际是权限不足。
        """
        sf = pw_env
        _add(sf, "mem-B", "user-B")
        _checkpoints(sf, "mem-B", 3, "B 的历史正文")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.post("/memory/mem-B/rollback"))

        assert resp.status_code == 403, (
            f"越权回滚经 REST 返回 {resp.status_code}（应为 403）：{resp.text}"
        )
        assert _row(sf, "mem-B").content == "mem-B 的当前正文", "经 REST 越权改写了 B 的正文"

    def test_mcp_handler_refuses_cross_user(self, pw_env):
        """MCP `rollback` 工具同样过归属（宿主透传 user_id 时）。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")
        _checkpoints(sf, "mem-B", 3, "B 的历史正文")

        from lantai.cli.mcp import handle_rollback

        res = handle_rollback({"memory_id": "mem-B", "user_id": "user-A"})

        assert res.get("ok") is False, f"MCP 越权回滚返回 ok：{res}"
        assert _row(sf, "mem-B").content == "mem-B 的当前正文", "MCP 改写了 B 的正文"

    def test_tenant_mismatch_refused(self, pw_env):
        """**跨租户**：同 user_id 不同 tenant 也不可回滚。

        `ensure_can_delete` 的租户分支：资源与主体都非空且不等 → 拒绝。
        单用户部署下这个分支不触发，所以必须显式造一个不同 tenant 的主体。
        """
        sf = pw_env
        _add(sf, "mem-t", "shared-user", tenant_id="tenant-B")
        _checkpoints(sf, "mem-t", 3, "t 的历史正文")

        from lantai.evolution.promoter import rollback

        res = rollback(
            "mem-t",
            principal=Principal(
                user_id="shared-user",
                tenant_id="tenant-A",
                allowed_lanes=["general", "fact"],
                role="user",
            ),
        )

        assert res.get("ok") is False, f"跨租户回滚竟然成功：{res}"
        assert _row(sf, "mem-t").content == "mem-t 的当前正文", "跨租户改写了正文"

    def test_lane_not_allowed_refused(self, pw_env):
        """**泳道越权**：主体 allowed_lanes 不含该记忆的 lane → 拒绝。

        同 `ensure_can_delete` 的 lane 分支。这里刻意让 user_id 相同，
        把 lane 单独拎出来测——否则上面几条用例会把 lane 分支遮住。
        """
        sf = pw_env
        _add(sf, "mem-l", "user-A", lane="secret")
        _checkpoints(sf, "mem-l", 3, "l 的历史正文")

        from lantai.evolution.promoter import rollback

        principal = Principal(
            user_id="user-A", tenant_id=None, allowed_lanes=["general", "fact"], role="user"
        )
        res = rollback("mem-l", principal=principal)

        assert res.get("ok") is False, f"泳道越权回滚竟然成功：{res}"
        assert _row(sf, "mem-l").content == "mem-l 的当前正文", "泳道越权改写了正文"


# ── feedback ────────────────────────────────────────────────────


class TestFeedbackWriteOwnership:
    def _req(self, mem_id: str):
        from lantai.models.schemas import FeedbackReq

        return FeedbackReq(
            memory_id=mem_id,
            query="查询词",
            helped=True,
            user_accepted=True,
            hallucination_risk=0.0,
        )

    def test_other_users_memory_not_fed_back(self, pw_env):
        """**Red 2（决定性）**：A 给 B 的记忆记 feedback，B 的字段都不变。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.services.evolution_service import record_feedback_entry

        res = record_feedback_entry(self._req("mem-B"), principal=_principal("user-A"))

        row = _row(sf, "mem-B")
        assert row is not None
        assert row.use_count == 0, f"A 刷了 B 的 use_count：{row.use_count}"
        assert row.helpful_count == 0, f"A 刷了 B 的 helpful_count：{row.helpful_count}"
        assert row.importance == 0.9, f"A 改了 B 的 importance：{row.importance}"
        assert res.get("ok") is False, f"越权 feedback 竟然返回 ok：{res}"

    def test_own_memory_still_fed_back(self, pw_env):
        """Red 3（feedback 侧）：A 给自己的记忆记 feedback 照常生效。"""
        sf = pw_env
        _add(sf, "mem-A", "user-A")

        from lantai.services.evolution_service import record_feedback_entry

        res = record_feedback_entry(self._req("mem-A"), principal=_principal("user-A"))

        row = _row(sf, "mem-A")
        assert res.get("ok") is True, f"A 给自己的记忆记 feedback 失败了：{res}"
        assert row.use_count == 1, f"feedback 没生效：use_count={row.use_count}"

    def test_null_owner_memory_still_fed_back(self, pw_env):
        """Red 4（feedback 侧）：NULL 属主历史行仍可操作。"""
        sf = pw_env
        _add(sf, "mem-null", None)

        from lantai.services.evolution_service import record_feedback_entry

        res = record_feedback_entry(self._req("mem-null"), principal=_principal("user-A"))

        assert res.get("ok") is True, f"NULL 属主历史行被锁死：{res}"
        assert _row(sf, "mem-null").use_count == 1

    def test_admin_feeds_back_anything(self, pw_env):
        """Red 5（feedback 侧）：admin 全权。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.services.evolution_service import record_feedback_entry

        res = record_feedback_entry(self._req("mem-B"), principal=_principal(None, role="admin"))

        assert res.get("ok") is True, f"admin 没能记 feedback：{res}"
        assert _row(sf, "mem-B").use_count == 1

    def test_internal_call_unfiltered(self, pw_env):
        """Red 6（feedback 侧）：principal=None 内部调用行为不变。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.services.evolution_service import record_feedback_entry

        res = record_feedback_entry(self._req("mem-B"))

        assert res.get("ok") is True, f"内部调用被收窄了：{res}"
        assert _row(sf, "mem-B").use_count == 1

    def test_mcp_handler_refuses_cross_user(self, pw_env):
        """MCP `feedback` 工具同样过归属（宿主透传 user_id 时）。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.cli.mcp import handle_feedback

        res = handle_feedback(
            {
                "memory_id": "mem-B",
                "query": "q",
                "helped": True,
                "user_accepted": True,
                "user_id": "user-A",
            }
        )

        assert res.get("ok") is False, f"MCP 越权 feedback 返回 ok：{res}"
        assert _row(sf, "mem-B").use_count == 0, "MCP 刷了 B 的 use_count"

    def test_mcp_handler_without_user_id_unfiltered(self, pw_env):
        """MCP 不透传 user_id → 不过滤（不猜身份，票 10 口径）。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.cli.mcp import handle_feedback

        res = handle_feedback(
            {"memory_id": "mem-B", "query": "q", "helped": True, "user_accepted": True}
        )

        assert res.get("ok") is True, f"不透传身份时被误拒了：{res}"
        assert _row(sf, "mem-B").use_count == 1

    def test_route_returns_403(self, pw_env):
        """**Red 7（feedback 路由层）**：越权 feedback 经 REST 得 **403**。"""
        sf = pw_env
        _add(sf, "mem-B", "user-B")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/feedback",
                    json={
                        "memory_id": "mem-B",
                        "query": "q",
                        "helped": True,
                        "user_accepted": True,
                        "hallucination_risk": 0.0,
                    },
                ),
            )

        assert resp.status_code == 403, (
            f"越权 feedback 经 REST 返回 {resp.status_code}（应为 403）：{resp.text}"
        )
        assert _row(sf, "mem-B").use_count == 0, "经 REST 越权刷了 B 的 use_count"
