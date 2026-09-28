"""票 22 追加：terminal 两处 SSE 边推送是死代码 + 漏传身份。

`routes_terminal.py:132` / `:200`：

```python
node_edges = list_edges(n["id"])
for e in node_edges if isinstance(node_edges, list) else []:
    ... e.get("source_memory_id", "")
```

`list_edges` 返回的是 **dict** `{"edges": [...]}`，`isinstance(..., list)`
恒为 False——两个循环体从未执行过一次。terminal 的「正在加载记忆关系图谱」
那步一条边都没送出过，却照样 yield `{"edges": []}`。

不 mock：真实 in-memory SQLite + init_fts + 直调 terminal_graph。
"""

import pytest

from lantai.core.auth import Principal
from lantai.models.tables import MemoryEdge, MemoryItem


def _principal(user_id: str, *, admin: bool = False) -> Principal:
    return Principal(user_id=user_id, allowed_lanes=None, role="admin" if admin else "user")


@pytest.fixture()
def term_env(param_env):
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


def _graph(principal, domain: str = ""):
    """直调 terminal_graph（普通函数，非 generator）。"""
    from lantai.api.routes_terminal import terminal_graph

    return terminal_graph(domain=domain, limit=100, principal=principal)


def test_terminal_graph_returns_own_edges(term_env):
    """决定性：terminal_graph 必须真的把边送出来（不是恒空 list）。"""
    session_factory, _ = term_env
    _memory(session_factory, "mem-A", "user-A")
    _memory(session_factory, "mem-B", "user-A")
    _edge(session_factory, "edge-1", "mem-A", "mem-B")

    out = _graph(_principal("user-A"))

    assert out["nodes"], "节点集不该为空"
    edge_ids = {e["id"] for e in out["edges"]}
    assert "edge-1" in edge_ids, f"边没被送出（死代码复现）：{out['edges']}"


def test_terminal_graph_hides_others_edges(term_env):
    """决定性：A 的图谱里不能出现 B 的边（归属下传）。"""
    session_factory, _ = term_env
    _memory(session_factory, "mem-A", "user-A")
    _memory(session_factory, "mem-B", "user-B")
    _edge(session_factory, "edge-b", "mem-B", "mem-A")
    _edge(session_factory, "edge-a", "mem-A", "mem-A")

    out = _graph(_principal("user-A"))

    edge_ids = {e["id"] for e in out["edges"]}
    assert "edge-b" not in edge_ids, f"A 的图谱里出现了 B 的边：{out['edges']}"
    assert "edge-a" in edge_ids, f"自己的边反而不见了：{out['edges']}"


def test_terminal_graph_edge_shape(term_env):
    """送出的边要带全字段（id/source/target/relation/confidence）。"""
    session_factory, _ = term_env
    _memory(session_factory, "mem-A", "user-A")
    _memory(session_factory, "mem-B", "user-A")
    _edge(session_factory, "edge-1", "mem-A", "mem-B", relation="refines")

    out = _graph(_principal("user-A"))

    e = next(x for x in out["edges"] if x["id"] == "edge-1")
    assert e["source"] == "mem-A"
    assert e["target"] == "mem-B"
    assert e["relation"] == "refines"
    assert e["confidence"] == pytest.approx(0.8)


def test_terminal_chat_stream_passes_principal_to_list_edges(term_env):
    """决定性：chat stream Step 4 必须按新形状调 `list_edges` 并下传 principal。

    **为什么 patch hybrid_search**：这条路径的 nodes 来自 `hybrid_search`，
    测试环境没有向量库/embedding，nodes 恒为空——不断「边进了 SSE 流」，
    因为那测的是 hybrid_search 空转。patch 它返回一条固定命中，
    nodes 就非空，边推送逻辑才真正被走到。

    两件事一起锁：`list_edges` 收到 `principal`（M11），且返回值按
    `["edges"]` 取而不是 `isinstance(..., list)`（M10）。
    """
    session_factory, _ = term_env
    _memory(session_factory, "mem-A", "user-A")
    _edge(session_factory, "edge-1", "mem-A", "mem-A")

    import asyncio

    import lantai.api.routes_terminal as routes_terminal
    from lantai.api.routes_terminal import ChatReq, terminal_chat_stream

    calls = []
    real_list_edges = routes_terminal.list_edges

    def spy(memory_id, relation=None, principal=None):
        calls.append({"memory_id": memory_id, "principal": principal})
        return real_list_edges(memory_id, relation, principal=principal)

    def fake_hybrid(*args, **kwargs):
        return [{"memory": {"id": "mem-A", "content": "c", "domain": "user"}, "score": 1.0}]

    # hybrid_search 是函数内局部导入，patch 它所在的模块才生效
    import lantai.retrieval.hybrid as hybrid_mod

    routes_terminal.list_edges = spy
    real_hybrid = hybrid_mod.hybrid_search
    hybrid_mod.hybrid_search = fake_hybrid
    try:

        async def _collect():
            resp = await terminal_chat_stream(
                ChatReq(query="测试", domain="", top_k=8, force=True),
                principal=_principal("user-A"),
            )
            return [chunk async for chunk in resp.body_iterator]

        chunks = asyncio.run(_collect())
    finally:
        routes_terminal.list_edges = real_list_edges
        hybrid_mod.hybrid_search = real_hybrid

    assert calls, "list_edges 一次都没被调用——nodes 是空的，边推送逻辑没被走到"
    for c in calls:
        assert c["principal"] is not None, f"list_edges 没收到 principal：{c}"

    edge_events = [c for c in chunks if '"edges"' in c]
    assert edge_events, "SSE 里没有 edges 事件"
    assert "edge-1" in "".join(edge_events), f"边没进 SSE 流：{edge_events}"
