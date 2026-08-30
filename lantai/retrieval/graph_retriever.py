"""贯珠（ADR-0035）：基于图谱拓扑的二度语义联想与多跳召回。

提供：
1. expand_graph_associations: 从种子记忆出发，沿 MemoryEdge 展开 1~2 度 BFS 关联联想；
2. graph_augmented_search: 混合检索初筛 + 图拓扑扩召一体化搜索。
"""
from collections import deque
from typing import Any, Optional
from sqlmodel import Session, or_, select

from lantai.core.logger import logger
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage import db


def expand_graph_associations(
    seed_memory_ids: list[str],
    max_hops: int = 2,
    min_edge_conf: float = 0.5,
    max_expanded: int = 10,
    session: Optional[Session] = None,
) -> list[dict]:
    """沿实体图谱进行 1~2 步广度优先（BFS）联想遍历。"""
    seeds = [sid for sid in seed_memory_ids if sid]
    if not seeds or max_hops < 1:
        return []

    def _traverse(s: Session) -> list[dict]:
        visited = set(seeds)
        expanded: list[dict] = []
        queue = deque([(sid, 0) for sid in seeds])

        while queue and len(expanded) < max_expanded:
            curr_id, curr_hop = queue.popleft()
            if curr_hop >= max_hops:
                continue

            # 查找以 curr_id 为起点或终点的所有满足置信度要求的边
            edges = s.exec(
                select(MemoryEdge)
                .where(
                    or_(
                        MemoryEdge.source_memory_id == curr_id,
                        MemoryEdge.target_memory_id == curr_id,
                    )
                )
                .where(MemoryEdge.confidence >= min_edge_conf)
            ).all()

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

                # 读取邻居记忆项
                neighbor_item = s.get(MemoryItem, neighbor_id)
                if neighbor_item and neighbor_item.status == "active":
                    expanded.append({
                        "memory_id": neighbor_id,
                        "hop": next_hop,
                        "via_memory_id": curr_id,
                        "relation": edge.relation,
                        "edge_confidence": edge.confidence,
                        "content": neighbor_item.content,
                        "lane": neighbor_item.lane,
                        "domain": getattr(neighbor_item, "domain", "user"),
                    })
                    queue.append((neighbor_id, next_hop))
                    if len(expanded) >= max_expanded:
                        break

        logger.info(
            "贯珠：从 %d 个种子出发，经 %d 跳展开 %d 条图谱联想记忆",
            len(seeds), max_hops, len(expanded),
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
    domain: Optional[str] = None,
    session: Optional[Session] = None,
) -> dict:
    """图增强混合检索：混合初筛 + 拓扑二度联想。"""
    # 1. 混合检索初筛
    primary_results = hybrid_search(
        query=query,
        top_k=top_k,
        domain=domain,
        session=session,
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
    )

    return {
        "query": query,
        "primary_results": primary_results,
        "associated_memories": associated,
    }
