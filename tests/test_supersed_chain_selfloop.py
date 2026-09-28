"""票 23：`get_supersed_chain` 自指环——`as_target` 默认值吃掉调用方意图。

`get_supersed_chain` 沿取代链向前走，只想沿 source 方向查
（`current` 是被取代的旧值，边指向新值）。但 `get_edges` 的
`as_target` 默认 True，于是调用处两个都是 True、走 OR 分支，
每一步都把「指向 current 的边」也取回来——`edges[0]` 可能正是
指向 current 自己的那条，链尾就出现 `superseded_by == memory_id`
的自指环，且让链多跳一步。

`visited` 集合挡住了无限循环，挡不住**已经追加的假条目**——
所以这不是崩溃，是静默给出错数据。

真实库实证（`.scratch/readside-gaps/probe_22_exploit.py`）：
`get_chain('mem_01KZRNAJHTXJQCBJ8K10TBHRCQ')` 返回一条
`superseded_by` 等于自身的链，而 `source == 该 id` 的 supersedes
边实测 0 条——这条链根本不该存在。

全部不 mock：真实 in-memory SQLite + init_fts + 直调被测函数。
"""

import pytest

from lantai.models.tables import MemoryEdge
from lantai.storage.edges import get_edges, get_supersed_chain


@pytest.fixture()
def chain_env(param_env):
    """param_env 底座：内存 SQLite + 真实建表 + patch db.get_session。"""
    return param_env


def _edge(session_factory, src: str, tgt: str, relation: str, conf: float = 0.9) -> None:
    with session_factory() as s:
        s.add(
            MemoryEdge(
                id=f"edge_{src}_{tgt}",
                source_memory_id=src,
                target_memory_id=tgt,
                relation=relation,
                confidence=conf,
            )
        )
        s.commit()


def test_chain_stops_at_terminal_memory(chain_env):
    """决定性：B 不是任何 supersedes 边的 source → 链必须为空。

    当前实现返回一条自指环（`superseded_by == B`）→ 红。
    """
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes")

    chain = get_supersed_chain("mem-B")

    assert chain == [], f"B 不该有取代链，却拿到 {chain}"


def test_chain_is_exactly_one_hop_from_source(chain_env):
    """决定性：从 A 出发必须恰好一跳 A→B，不能多出假的 B→B。"""
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes", conf=0.75)

    chain = get_supersed_chain("mem-A")

    assert chain == [{"memory_id": "mem-A", "superseded_by": "mem-B", "confidence": 0.75}], (
        f"链应恰好一跳，实际 {chain}"
    )


def test_chain_walks_multi_hop_forward_only(chain_env):
    """多跳链：A→B→C，从 A 出发是两跳，且第三跳不存在（C 不是 source）。"""
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes", conf=0.9)
    _edge(session_factory, "mem-B", "mem-C", "supersedes", conf=0.8)

    chain = get_supersed_chain("mem-A")

    assert [(c["memory_id"], c["superseded_by"]) for c in chain] == [
        ("mem-A", "mem-B"),
        ("mem-B", "mem-C"),
    ], f"链应两跳且无自指尾巴，实际 {chain}"


def test_chain_picks_first_edge_when_multiple_supersede(chain_env):
    """同一 source 有多条 supersedes 出边时，取第一条（锁 edges[0] 不是 edges[-1]）。

    没有这条，把 `edges[0]` 换成 `edges[-1]` 的变异杀不掉——测试数据里每步
    只有一条边时两者恒等。
    """
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes", conf=0.9)
    _edge(session_factory, "mem-A", "mem-Z", "supersedes", conf=0.5)

    chain = get_supersed_chain("mem-A")

    assert len(chain) == 1, f"应只有一跳，实际 {chain}"
    assert chain[0]["superseded_by"] == "mem-B", f"应取第一条出边 mem-B，实际 {chain[0]}"
    assert chain[0]["confidence"] == 0.9


def test_chain_ignores_supports_edges(chain_env):
    """supports 边不是取代关系，不得进链（锁 relation 过滤）。"""
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supports", conf=0.9)

    assert get_supersed_chain("mem-A") == [], "supports 边不该进 supersedes 链"


def test_chain_ignores_incoming_supersedes(chain_env):
    """决定性锁方向：A→B 存在时，从 B 出发必须空链（B 没有出边）。

    这条同时是 M1/M2/M3 的对偶——只查「从 A 出发」的话，把方向写成
    只查入边也能让那条绿。
    """
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes", conf=0.9)

    assert get_supersed_chain("mem-B") == [], "B 不是任何边的 source，链必须为空"


def test_chain_terminates_on_cyclic_edges(chain_env):
    """A→B 且 B→A 的环：visited 必须挡住死循环，链长度有界。

    锁 visited 真的把 current 记进去——把 `visited.add(current)` 改成
    记别的值，这条会挂死或无限追加。
    """
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes", conf=0.9)
    _edge(session_factory, "mem-B", "mem-A", "supersedes", conf=0.8)

    chain = get_supersed_chain("mem-A")

    assert [(c["memory_id"], c["superseded_by"]) for c in chain] == [
        ("mem-A", "mem-B"),
        ("mem-B", "mem-A"),
    ], f"环上必须恰好两跳后停，实际 {chain}"


def test_get_edges_as_source_excludes_incoming(chain_env):
    """锁 `as_source=True, as_target=False` 的语义：只返出边，不返入边。

    这是自指环的根因层——调用方意图是「只沿 source 走」，
    而默认的 `as_target=True` 把它变成了 OR。
    """
    session_factory, _engine = chain_env
    _edge(session_factory, "mem-A", "mem-B", "supersedes")

    outgoing = get_edges("mem-B", relation="supersedes", as_source=True, as_target=False)
    incoming = get_edges("mem-B", relation="supersedes", as_source=False, as_target=True)

    assert outgoing == [], f"mem-B 没有出边，却返回 {outgoing}"
    assert [e.source_memory_id for e in incoming] == ["mem-A"]
