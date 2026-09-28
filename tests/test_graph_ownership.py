"""票 17：贯珠图检索补归属——A 的种子不再经一条边展开出 B 的记忆正文。

**先说影响**：`MemoryEdge` **有**归属四元组，却**没有任何一条查询按归属
过滤边**。`expand_graph_associations` 的边查询一个条件都不带，于是
**种子集明明已按 principal 收窄，边这一层照样跨用户**：任何一条从 A 的
记忆指向 B 的记忆的边（A 自己建的、B 自己建的、票 16 的无归属 apply 造的
supersedes、票 18 的 obsidian 造的 links 都算）都让 B 的记忆成为 A 种子的
「邻居」。随后 `s.get(MemoryItem, neighbor_id)` 按主键直读，
**`neighbor_item.content` 原样进 `associated_memories` 返回给 A**。
`min_edge_conf=0.5` 轻易满足，两跳足够。

唯一的过滤是 `allowed_lanes`——那是**泳道**检查不是**归属**检查，
A 和 B 通常共用默认泳道集。

**两处都要修**：边查询加归属 scope（边决定走哪条路），
`s.get` 取到行后补 `_owns`（行决定露出什么）。只滤边不滤行、或只滤行不滤边
都会漏——`session.get` 按主键直读、绕开 SQL scope，是票 14 踩过两次的盲区。

**纪律**：不 mock 被测函数的内部计算。这里 mock 的只有外部网络
（embed / classify_intent）与外部存储（vector_store.search）。
"""

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem

SECRET_B = "B 的银行密码是 9527"
SECRET_A = "A 的项目部署在本地内网服务器"


def _principal(user_id, *, role="user"):
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=None, role=role)


def _mem(engine, mem_id: str, user_id: str | None, content: str, *, lane="preference"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mem_id,
                user_id=user_id,
                memory_type="preference",
                key=f"k-{mem_id}",
                title=mem_id,
                content=content,
                lane=lane,
                domain="user",
                status="active",
                importance=0.5,
                decay_score=0.9,
                tier="working",
                use_count=0,
                helpful_count=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()


def _edge(engine, eid: str, src: str, dst: str, user_id: str | None, conf: float = 0.9):
    with Session(engine) as s:
        s.add(
            MemoryEdge(
                id=eid,
                source_memory_id=src,
                target_memory_id=dst,
                relation="supports",
                confidence=conf,
                user_id=user_id,
            )
        )
        s.commit()


@pytest.fixture()
def g_env():
    """真实临时 SQLite（边与行都在库里，不 mock 被测函数的任何内部计算）。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _expand(engine, seeds, *, hops=2, conf=0.5, principal="sentinel", lanes=None):
    from lantai.retrieval.graph_retriever import expand_graph_associations

    kwargs = {}
    if principal != "sentinel":
        kwargs["principal"] = principal
    with Session(engine) as s:
        return expand_graph_associations(
            seed_memory_ids=seeds,
            max_hops=hops,
            min_edge_conf=conf,
            session=s,
            allowed_lanes=lanes,
            **kwargs,
        )


def _contents(out):
    return [o.get("content", "") for o in out]


# ── Red 1 / 2：两个方向都不漏 ────────────────────────────────────


class TestGraphNoCrossUserLeak:
    def test_forward_edge_does_not_expand_others_content(self, g_env):
        """A 的种子经 A 自己建的边指向 B → 不展开出 B 的正文。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-A", "m-B", "user-A")

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"A 的种子展开出了 B 的正文：{_contents(out)}"
        )

    def test_reverse_edge_does_not_expand_others_content(self, g_env):
        """反向边（B → A）同样不漏——B 建的边指向 A，A 仍是端点。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-B", "m-A", "user-B")

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"反向边照样展开出 B 的正文：{_contents(out)}"
        )

    def test_two_hop_does_not_reach_others_content(self, g_env):
        """二跳：A → A 的记忆 → B 的记忆，第二跳不露。"""
        _mem(g_env, "m-1", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的另一条记忆")
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-1", "m-2", "user-A")
        _edge(g_env, "e2", "m-2", "m-B", "user-A")

        out = _expand(g_env, ["m-1"], principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"二跳到达了 B 的正文：{_contents(out)}"
        )

    def test_low_confidence_cross_user_edge_is_not_the_reason(self, g_env):
        """排除假绿：把边的 confidence 调到远高于门槛，仍然不漏。

        否则「没展开」可能只是因为 `min_edge_conf` 挡住了，与归属无关。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-A", "m-B", "user-A", conf=0.99)

        out = _expand(g_env, ["m-A"], conf=0.1, principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"confidence=0.99 且门槛 0.1 时仍漏出 B 的正文：{_contents(out)}"
        )


# ── 边层 scope：不沿别人的边走 ───────────────────────────────────


class TestGraphEdgeScopeNarrowsTraversal:
    """边层的可观测价值是**收窄遍历**，不是挡正文（差分探针实测结论）。

    `.scratch/readside-gaps/probe_edge_scope_leak.py` 穷举 729 种
    「3 个邻居行属主 × 3 条边属主」形状，把 `_edge_scope` 打回恒 None：

    - **正文泄露形状：0** —— 行层 `_owns` 已挡住所有跨用户正文
      （`s.get` 取到行后先判归属、不通过就不入队，所以 B 的行永远
      不会变成 `curr_id` 去带出下一跳）。
    - **遍历范围有差异：266** —— 边决定走哪条路。A 会沿着**不属于自己**
      的边走下去，把本该止步的图走到第三跳。

    所以这里锁的是**遍历边界**，不是「B 的正文没出现」。锁错了对象，
    边层的三个变异（恒 None / 不认 admin / 查询不套 scope）就全都杀不掉。
    """

    def test_does_not_walk_edges_owned_by_others(self, g_env):
        """第一跳就踩在 B 建的边上 → 整条链断在这里，第二跳无从谈起。

        三条行全是 A 的（排除行层干扰），`e2` 还是 A 自己建的边——
        没有边层 scope 时 `n2` 照样可达，差异只能来自边层。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的第二条记忆")
        _mem(g_env, "m-3", "user-A", "A 的第三条记忆")
        _edge(g_env, "e1", "m-A", "m-2", "user-B")  # B 建的边
        _edge(g_env, "e2", "m-2", "m-3", "user-A")  # A 建的边

        out = _expand(g_env, ["m-A"], hops=2, principal=_principal("user-A"))
        assert not out, (
            f"沿 user-B 的边走出去了：{[o.get('memory_id') for o in out]}；"
            "边层 scope 没收窄遍历（只滤行不滤边就漏这里）"
        )

    def test_same_shape_with_own_edge_does_expand(self, g_env):
        """排除假绿：把 `e1` 的属主换成 A，同一条链必须照常展开。

        否则「没展开」可能只是因为别的原因，与边层归属无关。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的第二条记忆")
        _mem(g_env, "m-3", "user-A", "A 的第三条记忆")
        _edge(g_env, "e1", "m-A", "m-2", "user-A")  # ← 只改这一行
        _edge(g_env, "e2", "m-2", "m-3", "user-A")

        out = _expand(g_env, ["m-A"], hops=2, principal=_principal("user-A"))
        ids = {o.get("memory_id") for o in out}
        assert {"m-2", "m-3"} <= ids, f"A 自己的边链被修废了：{ids}"

    def test_does_not_walk_reverse_edge_owned_by_others(self, g_env):
        """反向边同理：B 建的边指向 A，A 不能借它走到 B 那一侧。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的第二条记忆")
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-2", "m-A", "user-B")  # B 建的边，A 是终点

        out = _expand(g_env, ["m-A"], hops=2, principal=_principal("user-A"))
        assert not [o for o in out if o.get("memory_id") == "m-2"], (
            f"沿 user-B 建的反向边走到了 A 的 m-2：{[o.get('memory_id') for o in out]}"
        )

    def test_edge_scope_returns_none_for_admin(self, g_env):
        """admin 的边层 scope 必须是 None——不认 admin 会把运维/worker 挡死。

        这条是 `_edge_scope` 的直接口径断言：G2 把 admin 检查删掉时，
        这里会拿到一个收窄后的 scope 而不是 None。
        """
        from lantai.retrieval.graph_retriever import _edge_scope

        assert _edge_scope(_principal("user-A", role="admin")) is None
        assert _edge_scope(None) is None

    def test_edge_scope_narrows_for_plain_user(self, g_env):
        """普通主体的边层 scope 必须收窄，且必须放行 NULL 属主老边。

        `is_(None)` 那一半是单人部署的命门：真实库的边绝大多数是迁移前的
        历史行，漏掉它整张图断连（G3 就是删这一半）。
        """
        from sqlalchemy.dialects import sqlite as sqlite_dialect

        from lantai.retrieval.graph_retriever import _edge_scope

        scope = _edge_scope(_principal("user-A"))
        assert scope is not None, "普通主体拿到 None scope（边层没收窄）"
        compiled = str(
            scope.compile(dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "user-A" in compiled, f"scope 没收窄到 viewer：{compiled}"
        assert "IS NULL" in compiled.upper(), f"scope 漏了 NULL 属主老边：{compiled}"


# ── Red 3 / 4 / 5：不能修废 ──────────────────────────────────────


class TestGraphOwnerBoundary:
    def test_own_two_hop_still_expands(self, g_env):
        """A 自己的两跳关联照常展开（不能把图修废）。"""
        _mem(g_env, "m-1", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的第二条记忆")
        _mem(g_env, "m-3", "user-A", "A 的第三条记忆")
        _edge(g_env, "e1", "m-1", "m-2", "user-A")
        _edge(g_env, "e2", "m-2", "m-3", "user-A")

        out = _expand(g_env, ["m-1"], principal=_principal("user-A"))
        ids = {o.get("memory_id") for o in out}
        assert {"m-2", "m-3"} <= ids, f"A 自己的两跳被修废了：{ids}"

    def test_null_owner_edge_still_traversable(self, g_env):
        """NULL 属主老边照常可通行（单人部署不能断连）。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-legacy", None, "老数据：没有属主")
        _edge(g_env, "e1", "m-A", "m-legacy", None)

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"))
        assert len(out) == 1 and "老数据" in out[0].get("content", ""), (
            f"NULL 属主老边不可通行了：{_contents(out)}"
        )

    def test_null_owner_neighbor_row_still_visible(self, g_env):
        """边有归属、但邻居行是 NULL 属主老行 → 仍可见。

        边与行是两个维度：真实库里老边老行都是 NULL，只滤一边会误杀。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-legacy", None, "老数据：没有属主")
        _edge(g_env, "e1", "m-A", "m-legacy", "user-A")  # 边有归属，行没有

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"))
        assert len(out) == 1, f"NULL 属主邻居行被误杀：{_contents(out)}"

    def test_owner_edge_to_other_users_row_is_blocked(self, g_env):
        """反向：边是 NULL、行是 B 的 → 行层必须挡住（只滤边不滤行就漏这里）。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-A", "m-B", None)  # 老边无归属

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"边无归属时行层没挡住 B 的正文：{_contents(out)}"
        )

    def test_admin_and_none_see_whole_graph(self, g_env):
        """admin / principal=None 全图（worker 不能空转）。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _edge(g_env, "e1", "m-A", "m-B", "user-A")

        for p in (_principal("user-A", role="admin"), None):
            out = _expand(g_env, ["m-A"], principal=p)
            assert len(out) == 1 and SECRET_B in out[0].get("content", ""), (
                f"principal={p!r} 下别人的记忆不可见（worker 会空转）：{_contents(out)}"
            )

    def test_lane_filter_still_applies(self, g_env):
        """泳道过滤没被归属判定挤掉（两个维度都要在）。"""
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的另一条记忆", lane="work")
        _edge(g_env, "e1", "m-A", "m-2", "user-A")

        out = _expand(g_env, ["m-A"], principal=_principal("user-A"), lanes=["preference"])
        assert not out, f"泳道过滤失效：{_contents(out)}"


# ── 顺序依赖：不通过归属判定的邻居绝不能变成下一跳的 curr_id ──────


class TestGraphRejectedNeighborNeverBecomesNextHop:
    """锁住「先判归属、后入队」这个顺序。

    差分探针（`probe_edge_scope_leak.py`）证明：**当前代码结构下**，
    行层 `_owns` 一个人就挡住了全部跨用户正文——被拒的邻居在
    `queue.append` 之前就 `continue` 了，所以 B 的行永远不会变成
    `curr_id` 去带出下一跳。266 种形状零正文泄露，靠的就是这个顺序。

    这也意味着边层是**纵深防御**而非唯一防线。若将来有人重排这段
    （比如先入队再判归属、或把 `visited.add` 挪到判定之后），
    行层会瞬间不够用——这条测试就是那道护栏：顺序一变，这里就红。
    """

    def test_rejected_neighbor_cannot_bridge_to_next_hop(self, g_env):
        """B 的行夹在中间：A →(A 的边) B →(B 的边) NULL 属主老行。

        B 那一行被 `_owns` 拒掉后，绝不能拿它当 `curr_id` 去展开
        B 的边、带出 B 侧可见的 NULL 属主老行。若顺序被重排，
        A 就能借 B 的行当跳板摸到 B 那一侧的图。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-B", "user-B", SECRET_B)
        _mem(g_env, "m-legacy", None, "B 侧的老数据：没有属主")
        _edge(g_env, "e1", "m-A", "m-B", "user-A")  # A 的边（边层放行）
        _edge(g_env, "e2", "m-B", "m-legacy", "user-B")  # B 的边（边层本应拦）

        out = _expand(g_env, ["m-A"], hops=3, principal=_principal("user-A"))
        assert not [c for c in _contents(out) if SECRET_B in c], (
            f"借 B 的行当跳板带出了 B 的正文：{_contents(out)}"
        )
        assert not [c for c in _contents(out) if "B 侧的老数据" in c], (
            f"B 的行被拒后仍展开了 B 的边：{_contents(out)}"
        )

    def test_owner_boundary_same_shape_still_expands(self, g_env):
        """排除假绿：把中间行换成 A 自己的，同一条链必须走到第三跳。

        否则上面那条「没带出老数据」可能只是因为图本来就断了，
        与归属顺序无关。
        """
        _mem(g_env, "m-A", "user-A", SECRET_A)
        _mem(g_env, "m-2", "user-A", "A 的第二条记忆")
        _mem(g_env, "m-legacy", None, "老数据：没有属主")
        _edge(g_env, "e1", "m-A", "m-2", "user-A")
        _edge(g_env, "e2", "m-2", "m-legacy", "user-A")

        out = _expand(g_env, ["m-A"], hops=3, principal=_principal("user-A"))
        assert any("老数据" in c for c in _contents(out)), (
            f"中间行换成 A 自己后仍到不了第三跳：{_contents(out)}"
        )


# ── 下传链：`graph_augmented_search` 两处都要传 ──────────────────


class TestGraphAugmentedSearchThreading:
    """`principal` 必须同时到 `hybrid_search` 与 `expand_graph_associations`。

    此前一处都不传：初筛结果本身就没收窄（票 15 的 `vector_owner_filter`
    到不了这里），种子集带着别人的记忆，图那一层再漏一次。
    """

    def test_principal_reaches_both_stages(self, g_env, monkeypatch):
        from lantai.retrieval import graph_retriever

        seen: dict = {}

        def _fake_hybrid(**kw):
            seen["hybrid"] = kw.get("principal")
            return [{"memory": {"id": "m-A"}}]

        def _fake_expand(**kw):
            seen["expand"] = kw.get("principal")
            return []

        monkeypatch.setattr(graph_retriever, "hybrid_search", _fake_hybrid)
        monkeypatch.setattr(graph_retriever, "expand_graph_associations", _fake_expand)

        p = _principal("user-A")
        graph_retriever.graph_augmented_search(
            query="查询", top_k=5, session=Session(g_env), principal=p
        )
        assert seen.get("hybrid") is p, "principal 没传到 hybrid_search（初筛没收窄）"
        assert seen.get("expand") is p, "principal 没传到 expand_graph_associations"

    def test_primary_results_are_owner_scoped(self, param_env):
        """端到端：初筛不返回别人的记忆（用 `param_env`，不 mock 检索本身）。

        `param_env` 已 stub 好外部依赖（embed / vector store / reranker），
        这里只补一个「只回 A 自己种子」的 store——把「图这一层」单独隔出来看。
        """
        from lantai.retrieval import graph_retriever
        from lantai.retrieval import hybrid as hybrid_mod

        session_factory, _ = param_env
        engine = session_factory().get_bind()
        _mem(engine, "m-A", "user-A", SECRET_A)
        _mem(engine, "m-B", "user-B", SECRET_B)
        _edge(engine, "e1", "m-A", "m-B", "user-A")

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-A", "distance": 0.05}]

            def search_batch(self, *a, **kw):
                return []

        with patch.object(hybrid_mod, "get_vector_store", lambda: VS()):
            out = graph_retriever.graph_augmented_search(
                query="兰台项目部署",
                top_k=5,
                session=session_factory(),
                principal=_principal("user-A"),
            )

        assert not [c for c in _contents(out.get("associated_memories", [])) if SECRET_B in c], (
            f"associated_memories 漏出 B 的正文：{out.get('associated_memories')}"
        )
        assert not [p for p in out.get("primary_results", []) if SECRET_B in str(p)], (
            f"primary_results 漏出 B 的正文：{out.get('primary_results')}"
        )


# ── 路由层 ───────────────────────────────────────────────────────


class TestGraphRouteReadScope:
    """宿主打的是 HTTP，路由少传一次身份照样漏（票 12/13 都在路由层栽过）。"""

    def test_route_passes_principal(self, param_env):
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user
        from lantai.retrieval import graph_retriever
        from lantai.retrieval import hybrid as hybrid_mod

        session_factory, _ = param_env
        engine = session_factory().get_bind()
        _mem(engine, "m-A", "user-A", SECRET_A)
        _mem(engine, "m-B", "user-B", SECRET_B)
        _edge(engine, "e1", "m-A", "m-B", "user-A")

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-A", "distance": 0.05}]

            def search_batch(self, *a, **kw):
                return []

        seen: dict = {}
        real_aug = graph_retriever.graph_augmented_search

        def _spy(**kw):
            seen["principal"] = kw.get("principal")
            return real_aug(**kw)

        with (
            patch.object(hybrid_mod, "get_vector_store", lambda: VS()),
            patch.object(graph_retriever, "graph_augmented_search", _spy),
        ):
            app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
            try:
                with TestClient(app) as c:
                    r = c.post("/search/graph_expand", json={"query": "兰台项目部署"})
            finally:
                app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 200, f"/search/graph_expand 失败：{r.status_code} {r.text[:200]}"
        assert seen.get("principal") is not None, "路由没把身份传给 graph_augmented_search"
        body = r.json()
        assoc = body.get("associated_memories", [])
        assert not [a for a in assoc if SECRET_B in str(a.get("content", ""))], (
            f"HTTP 响应里漏出 B 的正文：{assoc}"
        )

    def test_mcp_threads_host_user_id(self, monkeypatch):
        """MCP 复用票 10 的 `_principal_from_params`：宿主透传才收窄。"""
        from lantai.cli import mcp as mcp_mod
        from lantai.retrieval import graph_retriever

        seen: dict = {}
        monkeypatch.setattr(
            graph_retriever,
            "graph_augmented_search",
            lambda **kw: seen.setdefault("principal", kw.get("principal")) or {},
        )

        mcp_mod.handle_graph_expand_search({"query": "兰台项目部署", "user_id": "user-A"})
        assert seen.get("principal") is not None and seen["principal"].user_id == "user-A", (
            "MCP 透传了 user_id 却没构造 principal"
        )

        seen.clear()
        mcp_mod.handle_graph_expand_search({"query": "兰台项目部署"})
        assert seen.get("principal") is None, (
            "MCP 没透传 user_id 时应留 None（不猜身份），实际却构造了 principal"
        )


# ── 不 mock 冒烟：真实调用主路径不炸 ─────────────────────────────


class TestGraphSmoke:
    def test_expand_smoke_no_mock(self, g_env):
        """真实构造最小输入直调 `expand_graph_associations`（测试纪律）。

        不 mock session / 边查询 / 行读取，只验主路径不炸、形状正确。
        """
        from lantai.core.ids import new_id
        from lantai.retrieval.graph_retriever import expand_graph_associations

        _mem(g_env, "s-1", None, "种子记忆")
        _mem(g_env, "n-1", None, "邻居记忆")
        _edge(g_env, new_id("edge"), "s-1", "n-1", None)

        with Session(g_env) as s:
            out = expand_graph_associations(["s-1"], max_hops=1, session=s)
        assert len(out) == 1
        assert out[0]["memory_id"] == "n-1"
        assert out[0]["hop"] == 1
        assert out[0]["via_memory_id"] == "s-1"
        assert out[0]["relation"] == "supports"
