"""记忆关系（边）管理"""

from sqlmodel import select

from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage import db


def create_edge(
    source_memory_id: str,
    target_memory_id: str,
    relation: str,
    confidence: float = 0.5,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    principal=None,
) -> MemoryEdge:
    """创建记忆关系。

    归属四元组（票 .scratch/ownership-gaps/03）：列在 tables.py:271-282
    早已存在，此前从不填。`DELETE /edges/{id}` 的 ACL（routes_edges.py:46）
    拿 `edge.user_id` 与 principal 比对——边不带属主，那道校验永远判
    「无归属不越权」，形同虚设。supersedes 边还会参与他人召回排序
    （hybrid.py:248 的 `_edge_cb` 无用户过滤），属主是审计的唯一起点。

    事前归属校验（票 .scratch/ownership-gaps/02）：边连的两条记忆都要
    校验——A 在 B 的记忆上建 supersedes 边，就能把 B 的记忆在 B 自己的
    检索里压到 A 的之下。不是读不到，是排序被改。校验放在**这里**而不是
    路由里，是因为 terminal/merge 等内部路径也建 supersedes 边；
    口径同票 05（改写他人记忆的关联关系 = 破坏性操作），复用
    `acl.ensure_can_delete` 单一真源。principal=None 仅限内部调用
    （CLI/eval/worker），不校验。
    """
    from lantai.core.ids import new_id

    with db.get_session() as s:
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            for mid in (source_memory_id, target_memory_id):
                mem = s.get(MemoryItem, mid)
                if mem is None:
                    from fastapi import HTTPException

                    raise HTTPException(404, f"memory not found: {mid}")
                ensure_can_delete(
                    principal,
                    resource_user_id=mem.user_id,
                    resource_tenant_id=mem.tenant_id,
                    lane=mem.lane,
                )

        edge = MemoryEdge(
            id=new_id("edge"),
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relation=relation,
            confidence=confidence,
            user_id=user_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        s.add(edge)
        s.commit()
        s.refresh(edge)
        return edge


def get_edges(
    memory_id: str, relation: str | None = None, as_source: bool = True, as_target: bool = True
) -> list[MemoryEdge]:
    """查询记忆关系"""
    with db.get_session() as s:
        stmt = select(MemoryEdge)
        if as_source and as_target:
            stmt = stmt.where(
                (MemoryEdge.source_memory_id == memory_id)
                | (MemoryEdge.target_memory_id == memory_id)
            )
        elif as_source:
            stmt = stmt.where(MemoryEdge.source_memory_id == memory_id)
        elif as_target:
            stmt = stmt.where(MemoryEdge.target_memory_id == memory_id)
        if relation:
            stmt = stmt.where(MemoryEdge.relation == relation)
        return s.exec(stmt).all()


def delete_edge(edge_id: str) -> bool:
    """删除记忆关系"""
    with db.get_session() as s:
        edge = s.get(MemoryEdge, edge_id)
        if edge:
            s.delete(edge)
            s.commit()
            return True
        return False


def get_supersed_chain(memory_id: str) -> list[dict]:
    """获取完整的 supersedes 链（追溯取代历史）

    只沿 source 方向走（票 .scratch/readside-gaps/23）：`current` 是被取代的
    旧值，边指向新值。`as_target` 必须显式 False——它的默认值是 True，
    两个都 True 会让 `get_edges` 走 OR 分支，每一步都把「指向 current 的边」
    也取回来，链尾于是出现 `superseded_by == memory_id` 的自指环，
    且让链多跳一步。`visited` 只挡无限循环，挡不住已经追加的假条目。
    """
    chain = []
    current = memory_id
    visited = set()
    while current and current not in visited:
        visited.add(current)
        edges = get_edges(current, relation="supersedes", as_source=True, as_target=False)
        if not edges:
            break
        chain.append(
            {
                "memory_id": current,
                "superseded_by": edges[0].target_memory_id,
                "confidence": edges[0].confidence,
            }
        )
        current = edges[0].target_memory_id
    return chain


def get_causal_chain(
    memory_id: str,
    direction: str = "forward",
    max_depth: int = 10,
) -> list[dict]:
    """格物（v0.31）：获取因果时序溯源链。

    direction:
      - "forward": 顺查下游事态（沿着 causes 与 leads_to 边，从 source 向 target 延伸）
      - "backward": 逆查前置诱因（沿着 triggered_by 正向查找 target，或沿 causes 逆向查找 source）
    """
    chain: list[dict] = []
    current = memory_id
    visited = {current}
    depth = 0

    while current and depth < max_depth:
        depth += 1
        next_hop = None

        if direction == "forward":
            # 正向推演：当前节点作为 source，沿着 causes / leads_to 到下游
            edges = get_edges(current, as_source=True, as_target=False)
            causal_edges = [
                e
                for e in edges
                if e.relation in ("causes", "leads_to") and e.target_memory_id not in visited
            ]
            if causal_edges:
                edge = causal_edges[0]
                next_hop = {
                    "edge_id": edge.id,
                    "from_id": edge.source_memory_id,
                    "to_id": edge.target_memory_id,
                    "relation": edge.relation,
                    "confidence": edge.confidence,
                }
                visited.add(edge.target_memory_id)
                current = edge.target_memory_id
        else:
            # 逆向溯源：
            # 1. 当前节点作为 source，通过 triggered_by 指向前置诱因 (target)
            edges_trig = get_edges(
                current, relation="triggered_by", as_source=True, as_target=False
            )
            trig_candidates = [e for e in edges_trig if e.target_memory_id not in visited]
            if trig_candidates:
                edge = trig_candidates[0]
                next_hop = {
                    "edge_id": edge.id,
                    "from_id": edge.source_memory_id,
                    "to_id": edge.target_memory_id,
                    "relation": edge.relation,
                    "confidence": edge.confidence,
                }
                visited.add(edge.target_memory_id)
                current = edge.target_memory_id
            else:
                # 2. 或者查找以当前节点为 target 的 causes 边（即上游 source causes 当前 target）
                edges_cause = get_edges(current, relation="causes", as_source=False, as_target=True)
                cause_candidates = [e for e in edges_cause if e.source_memory_id not in visited]
                if cause_candidates:
                    edge = cause_candidates[0]
                    next_hop = {
                        "edge_id": edge.id,
                        "from_id": edge.target_memory_id,
                        "to_id": edge.source_memory_id,
                        "relation": "triggered_by",  # 逆向视角
                        "confidence": edge.confidence,
                    }
                    visited.add(edge.source_memory_id)
                    current = edge.source_memory_id

        if next_hop:
            chain.append(next_hop)
        else:
            break

    return chain
