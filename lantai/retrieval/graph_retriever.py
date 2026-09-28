"""贯珠（ADR-0035）：基于图谱拓扑的二度语义联想与多跳召回。

提供：
1. expand_graph_associations: 从种子记忆出发，沿 MemoryEdge 展开 1~2 度 BFS 关联联想；
2. graph_augmented_search: 混合检索初筛 + 图拓扑扩召一体化搜索。
"""

from collections import deque

from sqlmodel import Session, or_, select

from lantai.core.acl import viewer_of
from lantai.core.logger import logger
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage import db


def _edge_scope(principal):
    """边的归属 scope（票 `.scratch/readside-gaps/17`）。

    `MemoryEdge` **有**归属四元组却从不按归属过滤——贯珠的边查询一个
    条件都不带，种子集明明已按 principal 收窄，边这一层照样跨用户：
    任何一条从 A 的记忆指向 B 的记忆的边（A 自己建的、B 自己建的、
    票 16 的无归属 apply 造的 supersedes、票 18 的 obsidian 造的 links
    都算）都让 B 的记忆成为 A 种子的「邻居」。

    口径同前 14 票：admin / `principal=None` → `None`（不过滤，worker 不能
    空转）；否则 `user_id == viewer OR IS NULL`。**NULL 属主老边必须可通行**
    ——真实库的边绝大多数是迁移前的历史行，判不可见会让整张图断连。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    viewer = viewer_of(principal)
    return (MemoryEdge.user_id == viewer) | (MemoryEdge.user_id.is_(None))


def _owns(mem, principal) -> bool:
    """行级归属判定（同 `memory_service._owns`，票 readside-gaps/15/17）。"""
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return True
    return (mem.user_id or None) in (None, viewer_of(principal))


def expand_graph_associations(
    seed_memory_ids: list[str],
    max_hops: int = 2,
    min_edge_conf: float = 0.5,
    max_expanded: int = 10,
    session: Session | None = None,
    allowed_lanes: list[str] | None = None,
    principal=None,
) -> list[dict]:
    """沿实体图谱进行 1~2 步广度优先（BFS）联想遍历。

    归属（票 `.scratch/readside-gaps/17`）：边查询补 `_edge_scope`，
    `s.get(MemoryItem, neighbor_id)` 取到行后补 `_owns`。**两处都要**——
    边决定走哪条路，行决定露出什么；`session.get` 按主键直读、绕开 SQL
    层 scope，是票 14 踩过两次的盲区。

    差分探针（`probe_edge_scope_leak.py`，729 种形状）实测的**当前分工**：
    行层 `_owns` 一个人就挡住了全部跨用户正文——被拒的邻居在
    `queue.append` 之前就 `continue`，所以 B 的行永远不会变成 `curr_id`
    去带出下一跳；边层的可观测价值是**收窄遍历**（不沿别人的边走，
    也不把边上的 `relation` / `confidence` / `via_memory_id` 交出去）。
    **别把 `queue.append` 挪到 `_owns` 判定之前**——那一挪，行层立刻不够用，
    `tests/test_graph_ownership.py::TestGraphRejectedNeighborNeverBecomesNextHop`
    会红。
    """
    seeds = [sid for sid in seed_memory_ids if sid]
    if not seeds or max_hops < 1:
        return []

    scope = _edge_scope(principal)

    def _traverse(s: Session) -> list[dict]:
        visited = set(seeds)
        expanded: list[dict] = []
        queue = deque([(sid, 0) for sid in seeds])

        while queue and len(expanded) < max_expanded:
            curr_id, curr_hop = queue.popleft()
            if curr_hop >= max_hops:
                continue

            # 查找以 curr_id 为起点或终点的所有满足置信度要求的边
            q = (
                select(MemoryEdge)
                .where(
                    or_(
                        MemoryEdge.source_memory_id == curr_id,
                        MemoryEdge.target_memory_id == curr_id,
                    )
                )
                .where(MemoryEdge.confidence >= min_edge_conf)
            )
            if scope is not None:
                q = q.where(scope)
            edges = s.exec(q).all()

            for edge in edges:
                neighbor_id = (
                    edge.target_memory_id
                    if edge.source_memory_id == curr_id
                    else edge.source_memory_id
                )
                if neighbor_id in visited:
                    continue

                visited.add(neighbor_id)
                next_hop = curr_hop + 1

                # 读取邻居记忆项（session.get 是归属盲区，须补判定）
                neighbor_item = s.get(MemoryItem, neighbor_id)
                if not neighbor_item or neighbor_item.status != "active":
                    continue
                if not _owns(neighbor_item, principal):
                    logger.info(
                        "贯珠：邻居 %s 不归属当前主体，不展开（宁 miss 不脏写）", neighbor_id
                    )
                    continue
                if allowed_lanes is not None and neighbor_item.lane not in allowed_lanes:
                    continue
                expanded.append(
                    {
                        "memory_id": neighbor_id,
                        "hop": next_hop,
                        "via_memory_id": curr_id,
                        "relation": edge.relation,
                        "edge_confidence": edge.confidence,
                        "content": neighbor_item.content,
                        "lane": neighbor_item.lane,
                        "domain": getattr(neighbor_item, "domain", "user"),
                    }
                )
                queue.append((neighbor_id, next_hop))
                if len(expanded) >= max_expanded:
                    break

        logger.info(
            "贯珠：从 %d 个种子出发，经 %d 跳展开 %d 条图谱联想记忆",
            len(seeds),
            max_hops,
            len(expanded),
        )
        return expanded

    if session is not None:
        return _traverse(session)
    with db.get_session() as s:
        return _traverse(s)


def graph_augmented_search(
    query: str,
    top_k: int = 5,
    max_hops: int = 2,
    min_edge_conf: float = 0.5,
    domain: str | None = None,
    session: Session | None = None,
    allowed_lanes: list[str] | None = None,
    principal=None,
) -> dict:
    """图增强混合检索：混合初筛 + 拓扑二度联想。

    归属（票 `.scratch/readside-gaps/17`）：`principal` 要传给**两处**——
    `hybrid_search` 与 `expand_graph_associations`。此前一处都不传：
    初筛结果本身就没收窄（票 15 修的 `vector_owner_filter` 到不了这里），
    种子集带着别人的记忆，图那一层再漏一次。
    """
    # 1. 混合检索初筛
    primary_results = hybrid_search(
        query=query,
        top_k=top_k,
        domain=domain,
        session=session,
        principal=principal,
    )
    if isinstance(primary_results, tuple):
        primary_results = primary_results[0]

    # 2. 提取种子 ID
    seed_ids = []
    for item in primary_results:
        sid = item.get("id") or item.get("memory", {}).get("id")
        if sid:
            seed_ids.append(sid)

    # 3. 展开图谱联想
    associated = expand_graph_associations(
        seed_memory_ids=seed_ids,
        max_hops=max_hops,
        min_edge_conf=min_edge_conf,
        session=session,
        allowed_lanes=allowed_lanes,
        principal=principal,
    )

    return {
        "query": query,
        "primary_results": primary_results,
        "associated_memories": associated,
    }
