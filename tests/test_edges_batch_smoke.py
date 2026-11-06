"""perf(terminal)：批量边查询 `list_edges_batch` / `get_edges_for_memories` 冒烟 + 归属。

terminal 图谱（前 50 节点）与 chat SSE Step 4（前 20 节点）此前逐节点调
`list_edges`，N+1 次查询。改为一次批量查询后，必须保证：

1. 主路径真跑通（AGENTS.md 测试纪律：核心函数不 mock 冒烟）；
2. 归属口径与 `list_edges` 逐字一致——另一个用户的边不得返回；
3. 每个 id 的结果与单独调 `list_edges` 相同（形状 + 内容）；
4. 路由仍然把边送出来。

全部不 mock：真实 in-memory SQLite（param_env）+ 真实行。
"""

import pytest

from lantai.core.auth import Principal
from lantai.models.tables import MemoryEdge, MemoryItem


def _principal(user_id: str, *, admin: bool = False) -> Principal:
    return Principal(user_id=user_id, allowed_lanes=None, role="admin" if admin else "user")


@pytest.fixture()
def batch_env(param_env):
    return param_env


def _memory(session_factory, mid: str, user_id) -> None:
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=f"{mid} 的内容",
                lane="fact",
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


def _seed(sf) -> None:
    _memory(sf, "mem-A1", "user-A")
    _memory(sf, "mem-A2", "user-A")
    _memory(sf, "mem-A3", "user-A")
    _memory(sf, "mem-N", None)  # 历史行：属主 NULL，对所有人可见
    _memory(sf, "mem-B1", "user-B")
    _edge(sf, "e-a12", "mem-A1", "mem-A2")
    _edge(sf, "e-a23", "mem-A2", "mem-A3", relation="refines")
    _edge(sf, "e-self", "mem-A1", "mem-A1")
    _edge(sf, "e-an", "mem-A3", "mem-N")
    _edge(sf, "e-ab", "mem-A1", "mem-B1")  # 一端属 B → A 不可见
    _edge(sf, "e-b", "mem-B1", "mem-B1")
    _edge(sf, "e-ghost", "doc_x", "mem-A2")  # 端点不存在 → 不可见


def test_storage_get_edges_for_memories_smoke(batch_env):
    """不 mock 冒烟：一次查询取回 source/target 命中任一 id 的边，按 id 去重。"""
    from lantai.storage.edges import get_edges_for_memories

    sf, _ = batch_env
    _seed(sf)

    got = get_edges_for_memories(["mem-A1", "mem-A2", "mem-A1", ""])
    ids = [e.id for e in got]
    assert len(ids) == len(set(ids)), f"跨 id 未去重：{ids}"
    assert set(ids) == {"e-a12", "e-a23", "e-self", "e-ab", "e-ghost"}

    assert {e.id for e in get_edges_for_memories(["mem-A2"], relation="refines")} == {"e-a23"}
    assert get_edges_for_memories([]) == []


def test_list_edges_batch_matches_list_edges(batch_env):
    """不 mock 冒烟：每个 id 的结果与单独调 `list_edges` 逐字相同（含归属过滤）。"""
    from lantai.services.edge_service import list_edges, list_edges_batch

    sf, _ = batch_env
    _seed(sf)
    ids = ["mem-A1", "mem-A2", "mem-A3", "mem-N", "mem-B1", "mem-missing"]

    for principal in (
        _principal("user-A"),
        _principal("user-B"),
        _principal("x", admin=True),
        None,
    ):
        batch = list_edges_batch(ids, principal=principal)
        assert set(batch) == set(ids)
        for mid in ids:
            single = list_edges(mid, principal=principal)["edges"]
            key = lambda d: d["id"]  # noqa: E731
            assert sorted(batch[mid], key=key) == sorted(single, key=key), (
                f"{mid} / {principal}: batch={batch[mid]} single={single}"
            )


def test_list_edges_batch_hides_other_users_edges(batch_env):
    """归属：A 的批量结果里不得出现只要一端属于 B 的边，也不得出现幽灵端点边。"""
    from lantai.services.edge_service import list_edges_batch

    sf, _ = batch_env
    _seed(sf)

    out = list_edges_batch(["mem-A1", "mem-A2", "mem-A3", "mem-B1"], principal=_principal("user-A"))
    all_ids = {e["id"] for lst in out.values() for e in lst}
    assert "e-ab" not in all_ids, f"A 看到了连到 B 记忆的边：{out}"
    assert "e-b" not in all_ids, f"A 看到了 B 的边：{out}"
    assert "e-ghost" not in all_ids, f"幽灵端点边泄露：{out}"
    assert out["mem-B1"] == []
    assert {"e-a12", "e-a23", "e-self", "e-an"} <= all_ids

    e = next(x for x in out["mem-A2"] if x["id"] == "e-a23")
    assert e == {
        "id": "e-a23",
        "source": "mem-A2",
        "target": "mem-A3",
        "relation": "refines",
        "confidence": pytest.approx(0.8),
    }


def test_terminal_graph_still_returns_edges_via_batch(batch_env):
    """路由：terminal_graph 走批量后仍送出自己的边、去重、隐藏他人的边。"""
    from lantai.api.routes_terminal import terminal_graph

    sf, _ = batch_env
    _seed(sf)

    out = terminal_graph(domain="", limit=100, principal=_principal("user-A"))
    ids = [e["id"] for e in out["edges"]]
    assert len(ids) == len(set(ids)), f"graph 边未去重：{ids}"
    assert {"e-a12", "e-a23", "e-self", "e-an"} <= set(ids)
    assert "e-ab" not in ids and "e-b" not in ids and "e-ghost" not in ids
    assert set(out) == {"nodes", "edges"}


def test_terminal_graph_degrades_when_batch_fails(batch_env, monkeypatch):
    """批量查询异常：不再静默吞掉——记日志，接口仍返回节点 + 空边。"""
    import lantai.api.routes_terminal as rt

    sf, _ = batch_env
    _seed(sf)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(rt, "list_edges_batch", boom)
    out = rt.terminal_graph(domain="", limit=100, principal=_principal("user-A"))
    assert out["nodes"], "节点不该因为边查询失败而丢失"
    assert out["edges"] == []
