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
    """获取完整的 supersedes 链（追溯取代历史）"""
    chain = []
    current = memory_id
    visited = set()
    while current and current not in visited:
        visited.add(current)
        edges = get_edges(current, relation="supersedes", as_source=True)
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
