"""verbatim 检索读侧归属：票 .scratch/readside-gaps/07

`GET /verbatim/search` 调 `hybrid_search(...)` 时**不传 principal**，
而底层检索内核本来就支持归属过滤（`hybrid_search` 有 `principal` 形参，
`search_fts` / `search_fts_bm25` / LIKE 兜底都会加 `AND m.user_id = ?`）。
同文件 `routes_search.py:36`（主 `/search`）和 `routes_terminal.py:93`
都传了，**只有这一条忘了**。

后果（第三轮探测实测）：A 的身份打 `?q=连接池`，
拿到整条 B 的 `MemoryItem`——`content` 全文 + ULID `id` + `user_id`。

**修法口径：沿用检索内核既有的严格 `user_id = ?`，不另造 `OR IS NULL`。**
这与票 03/04 的 `OR IS NULL` 分歧是刻意的，理由见票 07 修法口径第 1 条：
检索是**按 query 相关性召回**，NULL 老行混进来只会稀释排序；
认知上下文/日报是**全量装配**，看不见就是失明。

**本文件刻意不设 `tenant_id`**（这是实测出来的关键约束，不是随手写的）：
`get_current_user` 只在收到 `X-Tenant-Id` 请求头时才给 principal 一个
tenant（`auth.py:143`），而 `fts.py:156` / `hybrid.py:433` 一旦拿到
tenant 就无条件加 `AND m.tenant_id = ?`——**连 admin 都不豁免**。
本机真实库 650/650 行 `tenant_id` 全为 NULL（实测），
于是 `tenant_id="t1"` 的 principal 会把**每一行**都滤掉，包括自己的。
早先本文件的 helper 就是那么写的，结果 owner / admin 两条测试拿到空集
——那不是产品缺陷，是**测试构造了生产不可能出现的身份**。
真实部署只有两种 tenant 状态：不带头（tenant=None）或带头（tenant 有值）。

**`user_id=None` 的 admin 是另一条真实状态**：`acl.Principal` 允许
`user_id=None`（`SYSTEM_PRINCIPAL` 就是），此时 `fts.py:158` 的
`if getattr(principal, "user_id", None)` 不成立 → 不加 user 过滤 →
admin 全权。这正是 `test_admin_sees_all` 采用的形态。
写入侧不归本票管：`add_raw_memory` 已有 `user_id` 形参（默认 "default"）。
"""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.schemas import RawMemoryReq
from lantai.services.memory_service import add_raw_memory

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str | None, *, role: str = "user"):
    """构造与真实部署一致的身份。

    刻意 `tenant_id=None`：本机 650/650 行 tenant 为 NULL，且
    `fts.py:156` 一旦拿到 tenant 就无条件加 `AND m.tenant_id = ?`
    （admin 也不豁免），带 tenant 的 principal 会滤掉包括自己在内的每一行。
    见模块 docstring。
    """
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def verbatim_env():
    """内存 SQLite 真实建表 + FTS 初始化 + patch 仅外部依赖。

    与 `test_verbatim_obsidian.py` 的 `obs_env` 同构（FTS 必须真初始化，
    否则 BM25 那条路是空的，测试会假绿）。
    """
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.services.memory_service.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
    ):
        yield session_factory, engine


def _seed(session_factory) -> None:
    """A 和 B 各存一条 verbatim 原文（写入侧已有 user_id 形参，直接用它）。"""
    add_raw_memory(RawMemoryReq(content="A 的原文：连接池 500，副本三台"), user_id="user-A")
    add_raw_memory(RawMemoryReq(content=SECRET), user_id="user-B")


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _search(principal, q: str = "连接池"):
    """以 principal 身份打一次真实 `/verbatim/search`，返回正文列表。"""
    from lantai.api.app import app

    with TestClient(app) as c:
        resp = _as(principal, lambda: c.get("/verbatim/search", params={"q": q}))
    assert resp.status_code == 200, resp.text
    return [r.get("memory", {}).get("content", "") for r in resp.json()]


# ── Red 1（决定性）：/verbatim/search 不再跨用户吐 ────────────────


class TestVerbatimSearchOwnership:
    def test_excludes_other_users_memories(self, verbatim_env):
        """A 搜 verbatim 不该拿到 B 的原文全文与 id。"""
        session_factory, _ = verbatim_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/verbatim/search", params={"q": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text, "A 读到了 B 的 verbatim 原文全文"
        assert "user-B" not in resp.text, "A 的响应里出现了 B 的 user_id"

    def test_owner_still_sees_own(self, verbatim_env):
        """A 自己的 verbatim 照旧能搜到（不能修废）。"""
        session_factory, _ = verbatim_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/verbatim/search", params={"q": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        texts = [r.get("memory", {}).get("content", "") for r in body]
        assert any("A 的原文" in t for t in texts), f"A 自己的原文搜不到了：{texts}"
        assert not any(SECRET in t for t in texts), f"A 搜到了 B 的原文：{texts}"

    def test_admin_sees_all(self, verbatim_env):
        """admin 照见全部（不能把运检索流程修废）。

        admin 走 `user_id=None` 形态（`acl.SYSTEM_PRINCIPAL` 就是这样）：
        `fts.py:158` 的 `if getattr(principal, "user_id", None)` 不成立，
        于是不加 user 过滤 → 全权。**这是 admin 在检索内核里唯一的放行形态**——
        带 `user_id="admin-1"` 的 admin 反而会被滤成只剩自己的行
        （实测如此，检索内核没有 role 判断，`grep is_admin lantai/retrieval/` 为空）。
        """
        session_factory, _ = verbatim_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.get("/verbatim/search", params={"q": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        texts = [r.get("memory", {}).get("content", "") for r in resp.json()]
        assert any(SECRET in t for t in texts), f"admin 看不到全部：{texts}"

    def test_internal_call_unfiltered(self, verbatim_env):
        """principal=None（内部调用 / MCP）不加过滤——不改变既有行为。

        `cli/mcp.py:671` 的 verbatim 工具没有调用方身份，传 None 时
        `hybrid_search` 不加 `AND m.user_id = ?`，与现在逐字一致。
        """
        session_factory, _ = verbatim_env
        _seed(session_factory)

        from lantai.retrieval.hybrid import hybrid_search

        results = hybrid_search("连接池", top_k=5, memory_types=["verbatim"], use_rerank=False)
        texts = [r.get("memory", {}).get("content", "") for r in results]
        assert any(SECRET in t for t in texts), f"内部调用被收窄了（行为被改变）：{texts}"


# ── 已知代价的固化（票 07 修法口径第 1 条的 2026-09-28 修正）──────────


class TestNullOwnerTradeoff:
    """严格 `user_id = ?` 口径的**已知代价**：NULL 属主行对非 admin 不可见。

    这不是缺陷回归，是**刻意的、已记录的取舍**。固化它的意义：
    哪天有人想给检索内核加 `OR IS NULL`（让老行重新可见），
    这条会先失败，逼他先读票 07 里那段修正说明——
    那里记着三条不改的理由，以及真正该修的是写入侧
    （`obsidian_service.py:98` 不传 `user_id`）+ 391 行历史回填。

    判据必须落在真正会变的量上：直接种一行 NULL 属主的 verbatim，
    断言非 admin 搜不到、admin 搜得到——而不是泛泛断言"不含机密"。
    """

    def test_null_owner_row_invisible_to_non_admin(self, verbatim_env):
        session_factory, _ = verbatim_env
        # 直接落库绕开 add_raw_memory 的默认值，精确构造 NULL 属主
        from lantai.core.ids import new_id
        from lantai.core.time import utcnow
        from lantai.models.tables import MemoryItem
        from lantai.services.memory_service import sync_fts

        with session_factory() as s:
            mem = MemoryItem(
                id=new_id("mem"),
                memory_type="verbatim",
                key="null-owner-key",
                content="NULL 属主的老原文：连接池 888",
                lane="general",
                status="active",
                user_id=None,
                tenant_id=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(mem)
            s.flush()
            sync_fts(s, mem.id, mem.content)
            s.commit()

        texts = _search(_principal("default"))
        assert texts == [], f"NULL 属主行对非 admin 竟然可见：{texts}"

    def test_null_owner_row_visible_to_admin(self, verbatim_env):
        """同一行 NULL 属主，admin（user_id=None 形态）必须看得见。

        上一条 + 这一条一起证明：不可见的成因是"归属不匹配"，
        而不是 FTS 索引坏了或测试种子没写进去。
        """
        session_factory, _ = verbatim_env
        from lantai.core.ids import new_id
        from lantai.core.time import utcnow
        from lantai.models.tables import MemoryItem
        from lantai.services.memory_service import sync_fts

        with session_factory() as s:
            mem = MemoryItem(
                id=new_id("mem"),
                memory_type="verbatim",
                key="null-owner-key",
                content="NULL 属主的老原文：连接池 888",
                lane="general",
                status="active",
                user_id=None,
                tenant_id=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(mem)
            s.flush()
            sync_fts(s, mem.id, mem.content)
            s.commit()

        texts = _search(_principal(None, role="admin"))
        assert any("连接池 888" in t for t in texts), f"admin 也看不到 NULL 属主行：{texts}"

    def test_default_user_sees_own_verbatim(self, verbatim_env):
        """`default` 用户看得到自己的 verbatim——这是修复后真实部署的主路径。

        真实库 4 个 API key 全是 `user_id='default'`；
        `add_raw_memory` 默认也写 `'default'`。此条锁住"自己的行不被误滤"。
        """
        session_factory, _ = verbatim_env
        add_raw_memory(RawMemoryReq(content="default 的原文：连接池 42"), user_id="default")

        texts = _search(_principal("default"))
        assert any("连接池 42" in t for t in texts), f"default 看不到自己的 verbatim：{texts}"
