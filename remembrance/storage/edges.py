"""记忆关系（边）管理"""
from sqlmodel import select
from remembrance.storage import db
from remembrance.models.tables import MemoryEdge


def create_edge(source_memory_id: str, target_memory_id: str,
               relation: str, confidence: float = 0.5) -> MemoryEdge:
    """创建记忆关系"""
    from remembrance.core.ids import new_id
    edge = MemoryEdge(
        id=new_id("edge"),
        source_memory_id=source_memory_id,
        target_memory_id=target_memory_id,
        relation=relation,
        confidence=confidence,
    )
    with db.get_session() as s:
        s.add(edge)
        s.commit()
        s.refresh(edge)
        return edge


def get_edges(memory_id: str, relation: str | None = None,
             as_source: bool = True, as_target: bool = True) -> list[MemoryEdge]:
    """查询记忆关系"""
    with db.get_session() as s:
        stmt = select(MemoryEdge)
        if as_source and as_target:
            stmt = stmt.where(
                (MemoryEdge.source_memory_id == memory_id) |
                (MemoryEdge.target_memory_id == memory_id)
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
        chain.append({
            "memory_id": current,
            "superseded_by": edges[0].target_memory_id,
            "confidence": edges[0].confidence,
        })
        current = edges[0].target_memory_id
    return chain
