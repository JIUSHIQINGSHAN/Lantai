"""认知上下文读侧归属：票 .scratch/readside-gaps/03

`GET /cognitive/context` 与 `/cognitive/context/prompt` 一个身份都不取，
`CognitiveContextBuilder.build()` 里 `select(MemoryItem)` 是**全表**——
A 调一次就拿到全库记忆全文 + 每条记忆的 ULID id。比票 01 更严重：

- 没有 query 收窄的侥幸：`task` 参数只影响排序，不影响范围；
- `content` 一字不漏（`/memories` 截断到 160，这条不截）；
- **这个端点的用途就是把记忆喂给 Agent**，泄漏面直接是模型上下文。

**本票与票 01/02 的一处刻意分歧**：NULL 属主的老行在这里**仍然可见**。
真实库 `memoryitem` 650 行里 629 行 `user_id IS NULL`，且 4 把 API key
全是 `user_id='default'`——单人部署。套用票 01 的「NULL 一律不可见」会让
这个端点对唯一的真实用户返回空上下文，Agent 直接失明，那是把功能修废，
不是收窄。口径取 `get_core_memory` 那一支：`user_id == viewer OR IS NULL`。

测试策略同 ownership-gaps 系列：走 `param_env` 的真实内存库（**不要自己
patch `db.engine`**——conftest 有防污染护栏，那样写第二个用例就 setup 报错），
不 mock 内部计算；断言落在行为上（响应里不出现他人内容与 id）。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id="t1",
        allowed_lanes=["general", "fact"],
        role=role,
    )


def _seed(session_factory, *, with_null_owner: bool = False) -> None:
    """种两个用户的记忆；`with_null_owner` 额外种一条未记录归属的老行。"""
    now = utcnow()
    rows = [
        ("user-A", "A 的内容连接池500", "mem-user-A"),
        ("user-B", SECRET, "mem-user-B"),
    ]
    if with_null_owner:
        rows.append((None, "老行：迁移前写入的记忆连接池", "mem-null"))
    with session_factory() as s:
        for uid, content, mid in rows:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    namespace="default",
                    key=f"key-{mid}",
                    content=content,
                    lane="fact",
                    role=CognitiveRole.OBSERVATION,
                    status="active",
                    importance=0.5,
                    decay_score=1.0,
                    confidence=0.9,
                    use_count=0,
                    created_at=now,
                    updated_at=now,
                    user_id=uid,
                    tenant_id="t1" if uid else None,
                )
            )
        s.commit()


def _seed_owned(session_factory, mid: str, user_id: str, content: str) -> None:
    """额外种一条指定属主的记忆（给「内部调用收敛到 default」当判据）。"""
    now = utcnow()
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                namespace="default",
                key=f"key-{mid}",
                content=content,
                lane="fact",
                role=CognitiveRole.OBSERVATION,
                status="active",
                importance=0.5,
                decay_score=1.0,
                confidence=0.9,
                use_count=0,
                created_at=now,
                updated_at=now,
                user_id=user_id,
                tenant_id="t1",
            )
        )
        s.commit()


@pytest.fixture()
def app_isolated(param_env):
    """挂真实 app，并把 DB 依赖指到 param_env 的内存库；用完即还原。

    **必须显式 override，且 key 必须用路由自己绑定的那个函数对象**：
    `routes_cognitive.py` 写的是 `from lantai.storage.db import get_session`，
    这在本模块命名空间里留下原始函数；conftest 的
    `mp.setattr(db_module, "get_session", session_factory)` 只改
    `lantai.storage.db.get_session`，动不了这个别名。FastAPI 的
    `dependency_overrides` 按**对象同一性**查表，从 `lantai.storage.db`
    现取一次只会拿到被 patch 后的 `session_factory`——注册上去也永远匹配
    不上路由的依赖，于是路由继续打宿主真实库：种子写不进、断言恒真，
    第一版 Red 只红 3 条（另 3 条"通过"是假绿）就是踩在这里。

    `routes_work_items.py` 是在 service 里晚绑定调 `db.get_session()`，
    所以那边不需要 override。两种写法本仓都有，别想当然。
    """
    from lantai.api import routes_cognitive as routes_cognitive_mod
    from lantai.api.app import app

    session_factory, _ = param_env

    def _override_session():
        yield session_factory()

    route_dep = routes_cognitive_mod.get_session
    app.dependency_overrides[route_dep] = _override_session
    yield app
    app.dependency_overrides.pop(route_dep, None)


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _ids(items) -> list[str]:
    return [x.get("id") for x in items]


# ── Red 1（决定性）：/context 不再全表吐 ────────────────────────


class TestCognitiveContextReadOwnership:
    def test_context_excludes_other_users_memories(self, param_env, app_isolated):
        """A 调 /cognitive/context 不该看见 B 的记忆正文与 id。"""
        session_factory, _ = param_env
        _seed(session_factory)

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert SECRET not in resp.text, "A 读到了 B 的记忆全文"
        assert "mem-user-B" not in _ids(body["facts"]), (
            f"A 拿到了 B 的记忆 id：{_ids(body['facts'])}"
        )
        assert "mem-user-A" in _ids(body["facts"]), "A 自己的记忆该被看见（不能修废）"

    def test_context_prompt_excludes_other_users(self, param_env, app_isolated):
        """`/context/prompt` 渲染成 Markdown prompt——同样不能漏。

        这条更该锁：它输出的是"可直接喂给 Agent 的系统提示词"，
        最容易被当成安全输出而忽略。
        """
        session_factory, _ = param_env
        _seed(session_factory)

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context/prompt", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text, "A 的 prompt 里出现了 B 的记忆全文"
        assert "mem-user-B" not in resp.text, "A 的 prompt 里出现了 B 的记忆 id"

    def test_summary_excludes_other_users(self, param_env, app_isolated):
        """`/cognitive/summary` 同源同漏法（rules + failures 两个切面）。"""
        session_factory, _ = param_env
        _seed(session_factory)

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/summary", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text, "A 的摘要里出现了 B 的记忆正文"

    def test_admin_sees_all(self, param_env, app_isolated):
        """admin 照见全部（不能把 Agent 装配器修废）。"""
        session_factory, _ = param_env
        _seed(session_factory)

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("admin-1", role="admin"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(_ids(body["facts"])) == {"mem-user-A", "mem-user-B"}, (
            f"admin 看不到全部：{_ids(body['facts'])}"
        )

    def test_null_owner_rows_still_visible(self, param_env, app_isolated):
        """NULL 属主的老行对非 admin 仍可见——本票与票 01/02 的刻意分歧。

        真实库 650 行 memoryitem 里 629 行 NULL，且 4 把 API key 全是
        `user_id='default'`（单人部署）。若照票 01 判「NULL 不可见」，
        这个端点会对唯一的真实用户返回空上下文——Agent 失明，
        那不是收窄泄漏，是把功能修废。口径同 `get_core_memory`。
        """
        session_factory, _ = param_env
        _seed(session_factory, with_null_owner=True)

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        ids = _ids(resp.json()["facts"])
        assert "mem-null" in ids, f"NULL 属主的老行被藏了（单人部署会直接失明）：{ids}"
        assert "mem-user-A" in ids, f"A 自己的记忆被藏了：{ids}"
        assert "mem-user-B" not in ids, f"B 的记忆漏出来了：{ids}"

    def test_internal_call_converges_to_default(self, param_env, app_isolated):
        """principal=None（内部 middleware / MCP）按 "default" 收敛，不报错。

        `runtime/middleware.py` 的 `build_cognitive_summary` 是**环境式**
        中间件，拿不到调用方身份——它只能收敛到 "default"。这不是漏洞：
        它只输出 rules + failures 各 1 条且截断到 80 字符，且走围栏包装。
        收敛后它仍看得见自己的行 + NULL 老行（否则 middleware 会失明）。
        """
        session_factory, _ = param_env
        _seed(session_factory, with_null_owner=True)
        _seed_owned(session_factory, "mem-default", "default", "默认属主的内容连接池")

        from lantai.cognition.context import CognitiveContextBuilder

        with session_factory() as s:
            ctx = CognitiveContextBuilder(s).build(task="连接池", top_k=12)

        ids = _ids(ctx.facts)
        assert "mem-default" in ids, (
            f"内部调用该收敛到 default 并看见 default 归属的行，实际只见 {ids}"
        )
        assert "mem-null" in ids, f"内部调用看不见 NULL 老行（middleware 会失明）：{ids}"
        assert "mem-user-B" not in ids, f"内部调用漏了别人的记忆：{ids}"
        assert "mem-user-A" not in ids, f"内部调用漏了别人的记忆：{ids}"
