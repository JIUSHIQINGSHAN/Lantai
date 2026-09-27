"""`GET /memories` 属主隔离：票 .scratch/ownership-gaps/01

路由不传 `principal` → 归属过滤形同虚设 → 任意登录用户可读全库记忆全文
并拿到 ULID id（后者正是 supersedes 边注入需要的 ID 预言机）。

测试策略：真 FastAPI app + 真临时库 + `app.dependency_overrides` 注入两个
不同身份（同 tests/test_bixiao_deterministic.py:253 的既有范式）。
不 mock 任何内部计算——`build_memories_page` 的过滤逻辑本就正确且原样执行，
要验证的只是「路由有没有把身份传下去」。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryItem

# 三条记忆：A 两条（不同 lane）、B 一条、C 一条（不同租户）
_SEEDS = [
    ("mem_own_a_1", "A 的私有备忘：服务器在 A 机房", "t1", "u1", "a1", "s1", "fact"),
    ("mem_own_a_2", "A 的另一条：偏好绿茶", "t1", "u1", "a1", "s1", "general"),
    ("mem_own_b_1", "B 的私有备忘：数据库连接池上限是 100", "t2", "u2", "a2", "s2", "fact"),
]


def _seed(session_factory):
    with session_factory() as s:
        s.add_all(
            [
                MemoryItem(
                    id=mid,
                    content=content,
                    tenant_id=tenant,
                    user_id=user,
                    agent_id=agent,
                    session_id=sess,
                    lane=lane,
                    domain="user",
                    status="active",
                )
                for mid, content, tenant, user, agent, sess, lane in _SEEDS
            ]
        )
        s.commit()


def _principal(user_id, tenant_id, agent_id="a1", session_id="s1"):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        allowed_lanes=["fact", "general"],
        role="user",
    )


@pytest.fixture()
def client(param_env):
    """真 TestClient；每个测试后清掉依赖覆写，避免污染后续测试。"""
    session_factory, _ = param_env
    _seed(session_factory)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


class TestMemoriesRouteForwardsPrincipal:
    """Red：A 能看到 B 的记忆（现状）。Green：只能看自己的。"""

    def test_user_sees_only_own_memories(self, client):
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", "t1")
        resp = client.get("/memories")
        assert resp.status_code == 200, resp.text

        body = resp.json()
        items = body.get("memories") or body.get("items") or []
        ids = {m["id"] for m in items}
        contents = " ".join(m.get("content", "") for m in items)

        assert "mem_own_a_1" in ids, f"A 看不到自己的记忆（返回 {ids}）"
        assert "mem_own_a_2" in ids, f"A 看不到自己的记忆（返回 {ids}）"
        assert "mem_own_b_1" not in ids, f"**越权：A 读到了 B 的记忆 id（{ids}）**"
        assert "数据库连接池上限是 100" not in contents, (
            f"**越权：A 读到了 B 的记忆全文**（{contents!r}）"
        )

    def test_other_tenant_isolated(self, client):
        """换一个租户的同名 user 也看不到——隔离单位是 tenant+user。"""
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", "t_other")
        resp = client.get("/memories")
        items = resp.json().get("memories") or resp.json().get("items") or []
        ids = {m["id"] for m in items}
        assert not (ids & {"mem_own_a_1", "mem_own_a_2", "mem_own_b_1"}), (
            f"异租户用户读到了他人记忆：{ids}"
        )

    def test_id_oracle_closed(self, client):
        """本票的安全意义：不再向攻击者提供目标记忆的 ULID id。

        supersedes 边注入（票 ownership-gaps/02）需要知道对方记忆的 id；
        这个接口此前直接把它列出来。
        """
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", "t1")
        resp = client.get("/memories", params={"limit": 100})
        items = resp.json().get("memories") or resp.json().get("items") or []
        assert all(m["id"].startswith("mem_own_a") for m in items), (
            f"响应里出现非属主 id：{[m['id'] for m in items]}"
        )


class TestNoRegression:
    """护栏：既有正确路径与内部调用方不得被本票改动破坏。"""

    def test_terminal_graph_still_works(self, client):
        """`/terminal/graph` 本来就把 principal 传对了——同一 service，不得回归。"""
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", "t1")
        resp = client.get("/terminal/graph")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        nodes = body.get("nodes") or []
        ids = {n["id"] for n in nodes}
        assert "mem_own_a_1" in ids, f"属主记忆应出现在图谱里（{ids}）"
        assert "mem_own_b_1" not in ids, f"图谱越权返回了他人的记忆（{ids}）"

    def test_service_still_supports_principal_none(self, param_env):
        """`principal=None`（CLI/eval/worker 内部调用）语义不变：不过滤、全量返回。"""
        from lantai.services.memory_service import list_memories

        session_factory, _ = param_env
        _seed(session_factory)  # 本测试不用 client fixture，需自行 seed
        page = list_memories(limit=100, principal=None)
        items = page.get("memories") or page.get("items") or []
        ids = {m["id"] for m in items}
        assert {"mem_own_a_1", "mem_own_a_2", "mem_own_b_1"} <= ids, (
            f"principal=None 的全量语义被破坏（返回 {ids}）"
        )

    def test_row_exposes_owner_fields(self, param_env):
        """顺带：`_row` 补 user_id/tenant_id，让调用方能自查归属。"""
        from lantai.services.memory_service import list_memories

        session_factory, _ = param_env
        _seed(session_factory)
        page = list_memories(limit=100, principal=None)
        items = page.get("memories") or page.get("items") or []
        assert items, "前置条件：有数据"
        row = next(m for m in items if m["id"] == "mem_own_a_1")
        assert row.get("user_id") == "u1", f"row 缺 user_id：{sorted(row)}"
        assert row.get("tenant_id") == "t1", f"row 缺 tenant_id：{sorted(row)}"
