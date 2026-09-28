"""票 22：`GET /edges/{id}` 与 `/edges/{id}/supersed-chain` 读侧补归属。

`routes_edges.py` 四条路由里 `POST /edges` 与 `DELETE /edges/{id}` 都取了
身份并校验，只有两条读路由一个身份都不取——同票 19/20 的形状「同一个文件里
修了一条、漏了旁边那条」。`list_edges` → `storage/edges.py:get_edges` 按
`source == mid OR target == mid` 直查全表，无任何归属过滤。

**边自身属主判不了**（实测 `.scratch/readside-gaps/probe_22_edge_owner_dist.py`：
真实库 76/76 条边 `user_id` 全 NULL）——按边自身过滤对全库一条都不生效，
修了等于没修。归属必须落到**端点记忆**上：两端各自去 `memoryitem` 取属主，
`user_id == viewer OR IS NULL` 才放行。

全部不 mock：真实 in-memory SQLite + init_fts + patch db.get_session。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryEdge, MemoryItem


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
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
def edge_env(param_env):
    """param_env 底座：内存 SQLite + 真实建表 + patch db.get_session。"""
    return param_env


def _memory(session_factory, mid: str, user_id, *, lane: str = "fact") -> None:
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=f"{mid} 的内容",
                lane=lane,
                status="active",
                user_id=user_id,
            )
        )
        s.commit()


def _edge(session_factory, eid: str, src: str, tgt: str, relation: str = "supports") -> None:
    with session_factory() as s:
        s.add(
            MemoryEdge(
                id=eid,
                source_memory_id=src,
                target_memory_id=tgt,
                relation=relation,
                confidence=0.8,
            )
        )
        s.commit()


def _edge_ids(out: dict) -> set[str]:
    return {e["id"] for e in out["edges"]}


# ── service 层：list_edges 补 principal ────────────────────────


class TestListEdgesOwnership:
    def test_own_edge_still_listed(self, edge_env):
        """自己的边必须还能列出来（修归属不能把自己的数据修没）。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-A")
        _edge(session_factory, "edge-1", "mem-A", "mem-B")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-A", principal=_principal("user-A"))
        assert _edge_ids(out) == {"edge-1"}

    def test_others_edge_hidden(self, edge_env):
        """决定性：A 查 B 的记忆，B 的边一条都不能出现。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-b", "mem-B", "mem-A")
        _edge(session_factory, "edge-a", "mem-A", "mem-A")

        from lantai.services.edge_service import list_edges

        # A 查自己：只该看见自己那条，不该看见 B 连过来的那条
        out = list_edges("mem-A", principal=_principal("user-A"))
        assert _edge_ids(out) == {"edge-a"}, f"A 看见了不该看的边：{out['edges']}"

        # A 查 B 的记忆：一条都不该有
        out_b = list_edges("mem-B", principal=_principal("user-A"))
        assert _edge_ids(out_b) == set(), f"A 列出了 B 的边：{out_b['edges']}"

    def test_edge_hidden_when_only_target_is_others(self, edge_env):
        """决定性锁**两端都判**：source 是 A 自己、target 是 B 时也要隐藏。

        少了这条，把 `for mid in (source, target)` 改成只查 source，
        「A 查自己」那条照样绿（它的 target 也是 A）。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-cross", "mem-A", "mem-B")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-A", principal=_principal("user-A"))
        assert _edge_ids(out) == set(), f"A 看见了连到 B 的边：{out['edges']}"

    def test_edge_visible_when_only_source_is_others(self, edge_env):
        """对偶：target 是 A 自己、source 是 B 时同样要隐藏。

        M5 只查 source 的话，这条会把「source=B 的边」全过滤掉，
        与上一条合起来才锁住「两端都要判」。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-in", "mem-B", "mem-A")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-A", principal=_principal("user-A"))
        assert _edge_ids(out) == set(), f"A 看见了从 B 来的边：{out['edges']}"

    def test_null_owner_edge_visible(self, edge_env):
        """NULL 属主记忆上的边必须可见——历史行判不可见会让整张图断连。

        同票 17 口径：真实库 636 行 memoryitem 是 user_id IS NULL。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-legacy", None)
        _edge(session_factory, "edge-legacy", "mem-legacy", "mem-legacy")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-legacy", principal=_principal("user-A"))
        assert _edge_ids(out) == {"edge-legacy"}

    def test_admin_sees_all(self, edge_env):
        """admin 不过滤——否则运维排查看不到全图。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-b", "mem-B", "mem-A")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-B", principal=_principal("admin-X", admin=True))
        assert _edge_ids(out) == {"edge-b"}

    def test_no_principal_means_no_filter(self, edge_env):
        """principal=None（worker/MCP/CLI）不过滤——否则内部路径空转。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-b", "mem-B", "mem-A")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-B")
        assert _edge_ids(out) == {"edge-b"}

    def test_dangling_edge_owner_hidden(self, edge_env):
        """端点记忆不存在时（doc_* 脏边），边本身不构成泄漏但也不该硬塞。

        真实库 74/76 条边的 source 是 doc_* 且 memoryitem 里不存在。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _edge(session_factory, "edge-dangling", "doc_不存在", "mem-A")

        from lantai.services.edge_service import list_edges

        out = list_edges("mem-A", principal=_principal("user-A"))
        assert _edge_ids(out) == set(), f"A 看见了挂着幽灵端点的边：{out['edges']}"


# ── 路由层：两条读路由必须取身份 ──────────────────────────────


class TestEdgesRouteOwnership:
    def test_route_takes_identity(self, edge_env):
        """决定性：路由必须把 ctx 传给 service。

        没有这条，service 加了 principal 形参而路由不传，等于没修——
        正是票 20 `/obsidian/sync` 犯过的错。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-b", "mem-B", "mem-A")

        A = _principal("user-A")
        with TestClient(_app()) as client:
            resp = _as(A, lambda: client.get("/edges/mem-B"))
        assert resp.status_code == 200, resp.text
        assert _edge_ids(resp.json()) == set(), f"路由漏传身份，A 拿到了 B 的边：{resp.text}"

    def test_chain_route_takes_identity(self, edge_env):
        """supersed-chain 路由同样要取身份。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-B")
        _edge(session_factory, "edge-sup", "mem-A", "mem-B", relation="supersedes")

        A = _principal("user-A")
        with TestClient(_app()) as client:
            # A 查 B 的链：B 不是任何边的 source，且边属 A——两条都不该给 A
            resp = _as(A, lambda: client.get("/edges/mem-B/supersed-chain"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["chain"] == [], f"A 拿到了 B 的取代链：{resp.text}"

    def test_chain_route_hides_nonempty_foreign_chain(self, edge_env):
        """决定性：A 查 B 的**非空**链必须被过滤成空。

                上一条里 B 不是任何边的 source，链本来就空——那条测不出
        「路由没传 principal」。这条让 B 自己有一条两跳链，未过滤时
                A 能原样拿到 B 的整条取代路径。
        """
        session_factory, _ = edge_env
        _memory(session_factory, "mem-B1", "user-B")
        _memory(session_factory, "mem-B2", "user-B")
        _memory(session_factory, "mem-B3", "user-B")
        _edge(session_factory, "e-b1", "mem-B1", "mem-B2", relation="supersedes")
        _edge(session_factory, "e-b2", "mem-B2", "mem-B3", relation="supersedes")

        A = _principal("user-A")
        with TestClient(_app()) as client:
            resp = _as(A, lambda: client.get("/edges/mem-B1/supersed-chain"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["chain"] == [], (
            f"A 拿到了 B 的两跳取代链（路由漏传身份）：{resp.json()['chain']}"
        )

    def test_chain_route_returns_own_chain(self, edge_env):
        """对偶：自己的链必须原样返回（不能把正常功能修坏）。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A1", "user-A")
        _memory(session_factory, "mem-A2", "user-A")
        _edge(session_factory, "e-a1", "mem-A1", "mem-A2", relation="supersedes")

        A = _principal("user-A")
        with TestClient(_app()) as client:
            resp = _as(A, lambda: client.get("/edges/mem-A1/supersed-chain"))
        assert resp.status_code == 200, resp.text
        assert [h["superseded_by"] for h in resp.json()["chain"]] == ["mem-A2"]

    def test_own_route_still_works(self, edge_env):
        """自己的边经路由仍可读（不能把正常功能修坏）。"""
        session_factory, _ = edge_env
        _memory(session_factory, "mem-A", "user-A")
        _memory(session_factory, "mem-B", "user-A")
        _edge(session_factory, "edge-1", "mem-A", "mem-B")

        A = _principal("user-A")
        with TestClient(_app()) as client:
            resp = _as(A, lambda: client.get("/edges/mem-A"))
        assert resp.status_code == 200, resp.text
        assert _edge_ids(resp.json()) == {"edge-1"}


def _app():
    from lantai.api.app import app

    return app
